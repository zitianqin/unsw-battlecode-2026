"""Every headless Claude session the loop starts is lean (meta.LEAN_SESSION: no MCP servers, no skills), and every
coder's also loads only sig's user settings (driver.CODER_SESSION's --setting-sources user). From 2026-09-24 01:38
(95ba85f) until 0f29c09 and d65c886 (27 Sep ~14:00) coders ran with --dangerously-skip-permissions while sig's MCP
servers (supabase-prod among them) were in reach, reading replays opponents write (MISSES 17). Each known launch's
argv is built with the process call mocked; a scan of the source fails when a new place names the `claude` program
that isn't one of them, so a new launch path can't skip the flags unnoticed. The scan is a tripwire, not a parser: it
flags every string that could be the program, and a string that isn't one goes in NOT_LAUNCHES. Every launch also
runs through meta.private_tmp, which starts the session at home.WORKER_NICE: below sig's dev work, above the loop's
games (28 Sep 15:50: coders' nice-0 analysis scripts held this Mac's load at 22 on 10 cores and every Mac-gated game
behind them)."""

import testhome  # before anything that reads live paths
import ast
import contextlib
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import driver
import home
import judge
import meta
import quota

REPO = Path(__file__).resolve().parent.parent
# Every place in loop/, referee/ and tools/ (subfolders too) that names the `claude` program, as module:function (a
# subfolder's module as its path, e.g. sub/x); each is checked below. A new one fails
# test_every_claude_command_line_is_a_known_launch until it is added here and checked, or to NOT_LAUNCHES.
KNOWN = {"meta:worker_command", "driver:run_lane", "driver:resume_coder", "driver:fresh_merge_turn"}
# "claude": a transcript's speaker, and a process health.mac_waits names the session of, not a launch.
NOT_LAUNCHES = {"my_messages:said", "my_messages:check_times", "my_messages:check_asks",
                "health:session_of", "health:loop_process",  # loop_process recognises Claude processes by name
                "driver:detached_start"}  # a supervised --once looks for its claude ancestor


class Stop(Exception):
    pass


# The `claude` command inside meta.private_tmp's wrapper (or the command itself, unwrapped).
def claude_argv(command: list[str]) -> list[str]:
    return command[command.index("claude"):]


def has(argv: list[str], flags: tuple[str, ...]) -> bool:
    return any(argv[i:i + len(flags)] == list(flags) for i in range(len(argv)))


# The module:function of every string literal (not a docstring) that could name the program: "claude" itself (an argv's
# first item, `["claude"] + flags`, `shutil.which("claude")`, `CLAUDE = "claude"`), a path ending in /claude, or a
# shell line running `claude -p` (an f-string's text parts included).
def claude_sites() -> set[str]:
    found = set()
    for folder in ("loop", "referee", "tools"):
        for path in (REPO / folder).rglob("*.py"):
            if path.name.startswith("test_"):
                continue
            module = path.relative_to(REPO / folder).with_suffix("").as_posix()
            tree = ast.parse(path.read_text())
            docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                          if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                          and node.body and isinstance(node.body[0], ast.Expr)
                          and isinstance(node.body[0].value, ast.Constant)}

            def visit(node: ast.AST, where: str) -> None:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    where = node.name
                if (isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
                        and (node.value.strip() == "claude" or node.value.rstrip().endswith("/claude")
                             or "claude -p" in node.value)):
                    found.add(f"{module}:{where}")
                for child in ast.iter_child_nodes(node):
                    visit(child, where)

            visit(tree, "<module>")
    return found


class Launches(unittest.TestCase):
    def test_every_claude_command_line_is_a_known_launch(self) -> None:
        sites = claude_sites()
        self.assertEqual(sites - NOT_LAUNCHES, KNOWN, "a new claude launch: add it to KNOWN and check its flags below")
        # A stale exemption would let a launch later added in that function pass unchecked.
        self.assertLessEqual(NOT_LAUNCHES, sites, "an exemption names no site any more: remove it")

    # REFLECT 30 Sep 0311 F29: every launch passes the one usage gate (quota.gate, loop/quota.py) with its kind, so a
    # new launcher can't run through a usage pause. Each KNOWN launch function calls it (meta.worker_command raises
    # quota.Held; the driver's usage_gate waits), and every call of meta.worker_command names its kind (`usage=`).
    GATES = {"gate", "usage_gate"}

    def test_every_launch_passes_the_usage_gate(self) -> None:
        functions, unnamed = {}, []
        for folder in ("loop", "referee", "tools"):
            for path in (REPO / folder).rglob("*.py"):
                if path.name.startswith("test_"):
                    continue
                module = path.relative_to(REPO / folder).with_suffix("").as_posix()
                for node in ast.walk(ast.parse(path.read_text())):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        functions[f"{module}:{node.name}"] = node
                    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                            and node.func.attr == "worker_command"
                            and not any(k.arg == "usage" for k in node.keywords)):
                        unnamed.append(f"{module}:{node.lineno}")
        for site in sorted(KNOWN):
            calls = {n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
                     for n in ast.walk(functions[site]) if isinstance(n, ast.Call)}
            self.assertTrue(calls & self.GATES, f"{site} launches claude without the usage gate (quota.gate)")
        self.assertEqual(unnamed, [], "a meta.worker_command call names no usage kind")
        with self.assertRaises(KeyError):  # a kind neither held nor exempt
            meta.worker_command(Path("/work"), "{}", usage="brand-new")
        (home.loop_home() / quota.LEVEL).write_text(json.dumps({"level": "95%", "rank": quota.PAUSE,
                                                               "resets_at": time.time() + 600, "since": time.time()}))
        try:
            with self.assertRaises(quota.Held):
                meta.worker_command(Path("/work"), "{}", usage="backlog")
            meta.worker_command(Path("/work"), "{}", usage="judge")  # evaluation's own never waits
        finally:
            (home.loop_home() / quota.LEVEL).unlink()

    def test_the_scan_finds_a_launch_built_elsewhere(self) -> None:
        # The detector's own check: each way of naming the program, planted in a module, is found; a mention in a
        # docstring or in help text isn't.
        forms = {"listed": 'def go():\n    return ["claude", "-p", *FLAGS]\n',
                 "joined": 'def go():\n    return ["claude"] + ["-p", "--model", "opus"]\n',
                 "which": 'import shutil\ndef go():\n    return [shutil.which("claude"), "-p"]\n',
                 "absolute": 'def go():\n    return ["/opt/homebrew/bin/claude", "-p"]\n',
                 "variable": 'CLAUDE = "claude"\ndef go():\n    return [CLAUDE, "-p"]\n',
                 "shell": 'def go(m):\n    run(f"claude -p --model {m}")\n',
                 "prose": '"""Run claude -p yourself."""\ndef go():\n    print("run claude yourself")\n'}
        with tempfile.TemporaryDirectory() as directory:
            for folder in ("loop/sub", "referee", "tools"):
                (Path(directory) / folder).mkdir(parents=True)
            for name, text in forms.items():
                (Path(directory) / "loop" / f"{name}.py").write_text(text)
            (Path(directory) / "loop/sub/deep.py").write_text('def go():\n    return ["claude", "-p"]\n')
            with patch(f"{__name__}.REPO", Path(directory)):
                self.assertEqual(claude_sites(), {"listed:go", "joined:go", "which:go", "absolute:go",
                                                  "variable:<module>", "shell:go", "sub/deep:go"})

    def test_a_worker_and_every_command_it_starts_run_below_dev_work_and_above_the_games(self) -> None:
        self.assertLess(0, home.WORKER_NICE)
        self.assertLess(home.WORKER_NICE, home.GAME_NICE)
        probe = [sys.executable, "-c", "import os, subprocess, sys; print(os.getpriority(os.PRIO_PROCESS, 0)); "
                 "subprocess.run([sys.executable, '-c', 'import os; print(os.getpriority(os.PRIO_PROCESS, 0))'])"]
        done = subprocess.run(meta.private_tmp(probe), capture_output=True, text=True, check=True)
        expected = max(os.getpriority(os.PRIO_PROCESS, 0), home.WORKER_NICE)  # from a niced launcher too, not 20
        self.assertEqual(done.stdout.split(), [str(expected)] * 2)  # the session, and a command it runs

    def test_every_worker_command_is_launched_through_private_tmp(self) -> None:
        # The audit, report, REFLECT, digest, map-idea, rule-count and judge workers build meta.worker_command's argv;
        # each call outside a worker_command wrapper (mapideas.worker_command) sits inside a meta.private_tmp call.
        def name(call: ast.Call) -> str:
            return getattr(call.func, "attr", None) or getattr(call.func, "id", "")

        unwrapped = []
        for path in (REPO / "loop").glob("*.py"):
            if path.name.startswith("test_"):
                continue

            def visit(node: ast.AST, function: str, wrapped: bool) -> None:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    function = node.name
                if isinstance(node, ast.Call):
                    if name(node) == "worker_command" and function != "worker_command" and not wrapped:
                        unwrapped.append(f"{path.name}:{node.lineno}")
                    wrapped = wrapped or name(node) == "private_tmp"
                for child in ast.iter_child_nodes(node):
                    visit(child, function, wrapped)

            visit(ast.parse(path.read_text()), "<module>", False)
        self.assertEqual(unwrapped, [])

    def test_headless_workers_are_lean_and_read_only_user_settings(self) -> None:
        argv = meta.worker_command(Path("/work"), "{}", (Path("/reads"),), tools="Read", effort="low", usage="judge")
        self.assertTrue(has(argv, meta.LEAN_SESSION))
        self.assertTrue(has(argv, ("--setting-sources", "user")))
        self.assertTrue(has(argv, ("--append-system-prompt", meta.FOREGROUND)))  # MISSES 63

    def test_the_riser_study_worker_is_lean_foreground_opus_and_niced(self) -> None:
        # loop/risers.py's daily worker (launch_or_collect), launched with its Popen mocked on a prepared folder.
        import risers
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            work = state / "risers" / "1"
            work.mkdir(parents=True)
            (meta.protected_io(state, "risers", work) / "PROMPT.md").write_text("study")
            risers.save(state, {"pending": str(work), "started_at": 1.0, "first_seen": 1.0})
            with patch.object(risers.subprocess, "Popen", return_value=Mock(pid=1)) as popen:
                self.assertIn("launched", risers.tick(state))
            command = popen.call_args.args[0]
            self.assertIn("private_tmp", command)  # at home.WORKER_NICE, its own CLAUDE_CODE_TMPDIR
            argv = claude_argv(command)
            self.assertTrue(has(argv, meta.LEAN_SESSION), argv)
            self.assertTrue(has(argv, ("--append-system-prompt", meta.FOREGROUND)), argv)
            self.assertTrue(has(argv, ("--model", "opus")), argv)
            self.assertTrue(has(argv, ("--setting-sources", "user")), argv)

    def test_the_backlog_audit_worker_is_lean_foreground_opus_niced_and_read_only(self) -> None:
        # loop/backlog.py's daily worker (a loop/reports.py kind), launched with its Popen mocked on a prepared
        # folder: no shell and no Edit (it writes only its own report and edit list), nothing writable in the repo.
        import backlog
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            work = state / "backlog" / "1"
            work.mkdir(parents=True)
            (meta.protected_io(state, "backlog", work) / "PROMPT.md").write_text("audit")
            backlog.reports.save(state, backlog.KIND, {"pending": str(work), "first_seen": 1.0})
            with patch.object(backlog.reports.subprocess, "Popen", return_value=Mock(pid=1)) as popen:
                self.assertIn("launched", backlog.tick(state))
            command = popen.call_args.args[0]
            self.assertIn("private_tmp", command)  # at home.WORKER_NICE, its own CLAUDE_CODE_TMPDIR
            argv = claude_argv(command)
            self.assertTrue(has(argv, meta.LEAN_SESSION), argv)
            self.assertTrue(has(argv, ("--append-system-prompt", meta.FOREGROUND)), argv)
            self.assertTrue(has(argv, ("--model", "opus")), argv)
            self.assertTrue(has(argv, ("--setting-sources", "user")), argv)
            self.assertTrue(has(argv, ("--tools", "Read,Grep,Glob,Write")), argv)
            settings = json.loads(argv[argv.index("--settings") + 1])
            self.assertIn(f"Edit(/{backlog.REPO}/**)", settings["permissions"]["deny"])

    def test_the_judge_is_a_lean_confined_worker_told_to_wait_in_the_foreground(self) -> None:
        # loop/judge.py's launch (LOOP.md: Judged verdicts): meta.worker_command's argv, through meta.private_tmp and
        # started in its own folder, with no network and nothing writable but that folder.
        seen = []

        def popen(command, **kwargs):
            seen.append((command, kwargs))
            raise OSError("stopped here")
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "runs").mkdir()
            work, error = judge.launch(state, {"id": "E0001", "idea": {}, "verdict": "discarded", "reason": "r"},
                                       {"result.json": "{}"}, popen)
            self.assertTrue(error.startswith("could not launch"))
        ((command, kwargs),) = seen
        self.assertEqual(command[:5], meta.in_folder(work, [])[:5])
        self.assertEqual(command[5:9], meta.private_tmp([])[:4])  # at home.WORKER_NICE
        argv = claude_argv(command)
        self.assertTrue(has(argv, meta.LEAN_SESSION))
        self.assertTrue(has(argv, ("--setting-sources", "user")))
        self.assertTrue(has(argv, ("--append-system-prompt", meta.FOREGROUND)))
        self.assertTrue(kwargs["start_new_session"])
        settings = json.loads(argv[argv.index("--settings") + 1])
        self.assertEqual(settings["sandbox"]["network"]["deniedDomains"], ["*"])
        self.assertIn(f"Edit(/{state / 'runs'}/**)", settings["permissions"]["deny"])  # its judged.json's folder
        self.assertNotIn("--add-dir", argv)  # nothing to read beyond its own folder

    # REFLECT 2026-09-30 09:12 F4: a rule sig gave reached only the workers told it by hand. Every launch (each
    # function opening a worker's prompt with meta.worker_io) calls meta.brief, which leaves the briefing as it is at
    # launch in the worker's folder and has its prompt name it; coders get it appended (driver.research_context).
    def test_every_launch_puts_the_briefing_in_its_workers_folder(self) -> None:
        launches, briefed = set(), set()
        for path in (REPO / "loop").glob("*.py"):
            if path.name.startswith("test_"):
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    called = {c.func.attr for c in ast.walk(node)
                              if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)}
                    site = f"{path.stem}:{node.name}"
                    if "worker_io" in called:
                        launches.add(site)
                    if "brief" in called:
                        briefed.add(site)
        self.assertGreaterEqual(launches, {"judge:launch", "reports:start_worker", "rulecounts:review"})
        self.assertEqual(launches - briefed, set(), "a launch without meta.brief: its worker misses the briefing")
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "briefing.md").write_text("- teams sometimes play bad bots (sig, 30 Sep)\n")
            (state / "runs").mkdir()
            work, _ = judge.launch(state, {"id": "E0001", "idea": {}, "verdict": "discarded", "reason": "r"},
                                   {"result.json": "{}"}, Mock(side_effect=OSError("stopped here")))
            self.assertEqual((work / "briefing.md").read_text(), "- teams sometimes play bad bots (sig, 30 Sep)\n")
            io = meta.protected_io(state, "judge", work)
            prompt = (io / "PROMPT.md").read_text()
            self.assertIn(f"Read {work / 'briefing.md'} first", prompt)
            # A link the worker left there is replaced, never written through; a prompt naming it gets no second line.
            outside = state / "outside"
            (work / "briefing.md").unlink()
            (work / "briefing.md").symlink_to(outside)
            meta.brief(state, work, io)
            self.assertFalse(outside.exists())
            self.assertFalse((work / "briefing.md").is_symlink())
            self.assertEqual((io / "PROMPT.md").read_text(), prompt)
            with patch.object(driver, "STATE", state), patch.object(driver.online, "evidence_text", return_value=""):
                self.assertIn("teams sometimes play bad bots", driver.research_context())

    def assert_coder(self, command: list[str]) -> None:
        self.assertEqual(command[:4], meta.private_tmp([])[:4], command)  # at home.WORKER_NICE
        argv = claude_argv(command)
        self.assertTrue(has(argv, driver.CODER_SESSION), argv)
        self.assertTrue(has(argv, meta.LEAN_SESSION), argv)
        self.assertTrue(has(argv, ("--setting-sources", "user")), argv)

    # The command `call` hands to subprocess.`target`, which stops it there (a Popen at its communicate, saving the
    # text sent to self.inputs); the driver's other steps faked, and `extra` the driver functions returning the given
    # values.
    def run_patched(self, directory: str, target: str, call, **extra) -> list[str]:
        seen: list[list[str]] = []

        def stop(text=None, timeout=None):
            self.inputs.append(text)
            raise Stop

        def capture(command, *args, **kwargs):
            seen.append(command)
            if target == "Popen":
                return Mock(pid=1, communicate=Mock(side_effect=stop))
            stop(kwargs.get("input"))

        with contextlib.ExitStack() as stack:
            for p in (patch.object(driver, "STATE", Path(directory)),
                      patch.object(driver, "lane_settings", return_value="{}"),
                      patch.object(driver, "lane_env", return_value={}), patch.object(driver.meta, "unlock"),
                      patch.object(driver, "worker_status"),
                      patch.object(driver.subprocess, target, side_effect=capture),
                      *(patch.object(driver, name, return_value=value) for name, value in extra.items())):
                stack.enter_context(p)
            with self.assertRaises(Stop):
                call()
        (command,) = seen
        return command

    def test_a_coders_session_and_its_resumed_turns_are_lean_and_read_only_user_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            exp = driver.Experiment(id="E0001", idea={"id": "I1"}, lane=1, session={"session_id": "s-1"},
                                    verdict="discarded")
            clone = Path(directory) / "clone"
            clone.mkdir()
            self.inputs: list = []
            with self.subTest("run_lane"):
                self.assert_coder(self.run_patched(directory, "Popen", lambda: driver.run_lane(exp, clone),
                                                   lane_prompt="code it", worker_prompt=""))
            with self.subTest("run_resolve, resumed"):
                warm = driver.Experiment(id="E0001", idea={"id": "I1"}, lane=1,
                                         session={"session_id": "s-1", "coded_at": time.time()})
                self.assert_coder(self.run_patched(directory, "Popen", lambda: driver.run_resolve(warm, clone),
                                                   resolve_prompt="resolve it", git=""))
            with self.subTest("run_resolve, fresh"):  # no hand-in time: its coder's cache is taken as cold
                command = self.run_patched(directory, "Popen", lambda: driver.run_resolve(exp, clone),
                                           resolve_prompt="resolve it", merge_brief="brief ", git="")
                self.assert_coder(command)
                argv = claude_argv(command)
                self.assertNotIn("--resume", argv)
                self.assertEqual(argv[argv.index("--append-system-prompt") + 1], meta.FOREGROUND)  # MISSES 63
                self.assertEqual(self.inputs[-1], "brief resolve it")
            with self.subTest("run_postmortem"):
                self.assert_coder(self.run_patched(directory, "Popen", lambda: driver.run_postmortem(exp, clone),
                                                   prepare_postmortem="explain it"))
            with self.subTest("continue_turn"):
                self.assert_coder(self.run_patched(directory, "Popen",
                                                   lambda: driver.continue_turn(exp, clone, "waiting", True)))

    def test_every_coder_turn_is_told_to_wait_for_its_own_work_in_the_foreground(self) -> None:
        # MISSES 63: headless, nothing re-invokes a session whose turn ended while its games ran. The first launch
        # carries the rule as its system prompt; a resumed turn keeps that one, so its prompt text carries it too.
        with tempfile.TemporaryDirectory() as directory:
            exp = driver.Experiment(id="E0001", idea={"id": "I1"}, lane=1,
                                    session={"session_id": "s-1", "coded_at": time.time()}, verdict="discarded")
            clone = Path(directory) / "clone"
            clone.mkdir()
            self.inputs = []
            argv = claude_argv(self.run_patched(directory, "Popen", lambda: driver.run_lane(exp, clone),
                                                lane_prompt="code it", worker_prompt=" and report to bc-main"))
            self.assertEqual(argv[argv.index("--append-system-prompt") + 1], meta.FOREGROUND + " and report to bc-main")
            self.assertEqual(argv.count("--append-system-prompt"), 1)
            for name, call, extra in (("resolve", lambda: driver.run_resolve(exp, clone),
                                       {"resolve_prompt": "r", "git": ""}),
                                      ("postmortem", lambda: driver.run_postmortem(exp, clone),
                                       {"prepare_postmortem": "p"})):
                self.run_patched(directory, "Popen", call, **extra)
            self.assertEqual(self.inputs[1], f"r\n\n{meta.FOREGROUND}")
            # the post-mortem is also told its own session name (run_postmortem)
            self.assertRegex(self.inputs[2], r"^p\n\nThis turn's session name is bc-E0001-postmortem-\w{8}: ")
            self.assertTrue(self.inputs[2].endswith(f"\n\n{meta.FOREGROUND}"))
            self.assertEqual(len(self.inputs), 3)

    def test_the_foreground_rule_names_the_waits_that_never_come_back_and_one_that_works(self) -> None:
        # E0197: Claude Code's sleep block said "use Monitor with an until-loop"; the coder called Monitor and ended
        # its turn "I'll be notified" (52 of 186 coder transcripts hit that block, 68 used Monitor). A pid loop ends at
        # once in a coder's sandbox, which refuses kill -0; an until loop over a done file waited in a real session.
        for text in (meta.FOREGROUND, driver.CONTINUE):
            self.assertIn("Monitor", text)
            self.assertIn("run_in_background", text)
        self.assertIn("until [ -f <name>.done ]; do sleep 30; done", meta.FOREGROUND)
        # A rerun under the same name must not read the last run's exit code (the review's case).
        self.assertIn("`rm -f <name>.done; (<work>; echo $? > <name>.done)", meta.FOREGROUND)
        self.assertIn("600000 ms", meta.FOREGROUND)
        self.assertIn(meta.GENERALISE, meta.FOREGROUND)  # every headless session hears the generalisation rule
        self.assertNotIn("while kill -0", meta.FOREGROUND)


if __name__ == "__main__":
    unittest.main()
