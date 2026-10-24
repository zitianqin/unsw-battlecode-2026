#!/bin/bash
# Prints this Mac's CPU as the loop last published it (home.published_split, while under 30 s old): the cores busy at
# normal priority ("dev": sig's work, builds, tests, the loop's controllers), those the loop's niced games and its
# headless workers (home.WORKER_NICE) use (of them, the players' beside an evaluation at nice 20), and the 1-minute
# load average, which counts both and which sig's other sessions gate on (loop/LOOP.md "Niced games on this Mac").
#
#     tools/devload.sh
loop_dir="$(cd "$(dirname "$0")/../loop" && pwd)"
exec python3 -c 'import os, sys, time; sys.path.insert(0, sys.argv[1]); import home
split, at = home.published()
cores, load = os.cpu_count() or 1, os.getloadavg()[0]
text = ""
for path in (home.loop_home() / "load-fraction", home.load_fraction_copy()):  # the copy: in a coder sandbox
    try:
        text = path.read_text()
        break
    except PermissionError:
        continue
    except OSError:
        break
gate = min(home.load_share(text), home.SESSION_SHARE) * cores
if split is None:
    print(f"no CPU split published in the last {home.SPLIT_COPY_S} s (no loop game is gated here now)")
else:
    print(f"dev {split.dev:.2f} of {cores} cores, niced games and workers {split.niced:.2f} (beside an evaluation "
          f"{split.yielding:.2f}), measured {time.time() - at:.0f} s ago")
print(f"1-minute load {load:.2f}: niced games start only under {gate:.1f}")' "$loop_dir"
