"""Fast, editable C++ bot scenarios and protocol checks; no game engine or network.

Run: .venv/bin/python loop/bot_tests.py
Test a candidate: .venv/bin/python loop/bot_tests.py --bot PATH --tests PATH/tests/bot
The selected suite is authoritative: outdated tests may be changed, replaced or deleted.
"""

from __future__ import annotations

import argparse
import shutil
import signal
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import home

REPO = Path(__file__).resolve().parent.parent
SUITE = REPO / "tests/bot"
# libc++'s bounds and precondition checks in every scenario build (MISSES 86: a candidate wrote a vector's [-1] in play,
# which ASan missed in one game; this traps the case at the bad index). The champion's 30 scenarios and protocol
# suite took 47 s of CPU with it and without (29 Sep, one at a time, twice each way). A standard library other than
# libc++ ignores it.
HARDENING = "-D_LIBCPP_HARDENING_MODE=_LIBCPP_HARDENING_MODE_EXTENSIVE"
TRAPPED = "libc++ hardening check (out-of-bounds index or similar) trapped"  # what a case killed by SIGTRAP says


def run_tests(bot: Path, suite: Path = SUITE) -> int:
    """Run the selected cases against actual bot sources, without restoring old expectations."""
    sources = sorted(suite.glob("test_*.cpp"))
    protocol = suite / "test_protocol.py"
    has_protocol = protocol.exists()
    if not sources and not has_protocol:
        print("bot tests: no bot tests selected (0 checks); correctness coverage is absent", flush=True)
        return 0
    compiler = shutil.which("clang++") or shutil.which("c++")
    if compiler is None:
        raise RuntimeError("install a C++20 compiler (clang++ or c++) to run bot tests")
    cases = [*sources, *([bot / "main.cpp"] if has_protocol else [])]
    with tempfile.TemporaryDirectory(prefix="bc-bot-tests-") as directory, \
            ThreadPoolExecutor(max_workers=workers(len(cases))) as pool:
        # Each case compiles and runs apart; its output is printed whole, in the suite's order.
        def case(index: int, source: Path) -> tuple[str, str | None]:
            binary = Path(directory) / f"test-{index}"
            try:
                subprocess.run([compiler, "-std=c++20", "-O0", HARDENING, "-I", str(bot), "-I", str(suite),
                                str(source), "-o", str(binary)], check=True, timeout=120, capture_output=True,
                               text=True)
                command = [sys.executable, str(protocol), str(binary)] if source == bot / "main.cpp" else [str(binary)]
                done = subprocess.run(command, check=True, timeout=30, capture_output=True, text=True)
                return (done.stdout or "") + (done.stderr or ""), None
            except subprocess.CalledProcessError as error:
                trapped = error.returncode in (-signal.SIGTRAP, 128 + signal.SIGTRAP)  # a case, or a shell's 133
                return (error.stdout or "") + (error.stderr or ""), f"FAIL {source}: {error}" + (
                    f" ({TRAPPED})" if trapped else "")
            except (subprocess.SubprocessError, ValueError) as error:
                return "", f"FAIL {source}: {error}"

        results = list(pool.map(case, range(len(cases)), cases))
    failures = 0
    for output, failure in results:
        print(output, end="", flush=True)
        if failure:
            failures += 1
            print(failure, file=sys.stderr, flush=True)
    print(f"bot tests: {len(sources)} scenario binaries + {int(has_protocol)} protocol suite; {failures} failed",
          flush=True)
    return 1 if failures else 0


# How many cases compile and run at once: home.pool_size's, never waiting (the gate, tools/gated_test.sh, already let
# the run start): the cores the loop's load share leaves above the work at this run's priority or higher, so a
# coder's or a prepare's run (at home.WORKER_NICE) counts every other worker's tests and scripts, and two started
# together no longer take the share each (28-29 Sep: 2 of 26 prepared runs overlapped). 27 one at a time took ~37 s
# (REFLECT 2026-09-28 20:52 F27, the test-prune report).
def workers(cases: int) -> int:
    return home.pool_size(cases, wait=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bot", type=Path, default=REPO / "bot")
    parser.add_argument("--tests", type=Path, default=SUITE)
    args = parser.parse_args()
    try:
        return run_tests(args.bot.resolve(), args.tests.resolve())
    except (OSError, RuntimeError) as error:
        print(f"bot tests: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
