#!/bin/bash
# Runs a test command only under the load gate, and exits with the command's own status, so a failing test can't
# pass as success through a following `&& git commit` (MISSES 10):
#
#     tools/gated_test.sh .venv/bin/python -m unittest test_health test_evalline
#     tools/gated_test.sh .venv/bin/python -m unittest test_health && git commit ...
#     tools/gated_test.sh bash -o pipefail -c '.venv/bin/python -m unittest test_health 2>&1 | tail -3'
#
# The status is the command's own, so a pipe inside it returns its last stage's status unless that command sets
# pipefail itself, as the third line does (`sh -c 'false | cat'` exits 0).
#
# It waits, checking every 20 s and printing the load every 5 minutes, until the 1-minute load average is below the
# loop's load share x the core count: the share is home.load_share of <loop home>/load-fraction ($BC_LOOP_HOME, else
# ~/.bc-loop; 0.1-0.9, 0.7 when it is missing, or unreadable with no copy; read at every check), the gate the loop's
# own games keep; BC_GATE_URGENT=1 (the evaluation's critical path only: read_share) makes it 1, the hard stop. A
# coder's sandbox can't read it, so there it reads the copy the upkeep keeps for coders (home.load_fraction_copy). A
# load it can't read never passes. GATED_MAX_WAIT=<seconds> gives up after that long and exits 75 without running the
# command.
#
# Each reading tries its sources in turn and takes the first that prints a number, because a coder's sandbox (Claude
# Code's seatbelt profile) refuses the `sysctl` command outright while getconf, Python and `uptime` still work there
# (E0172, MISSES 54): cores from sysctl hw.ncpu, getconf, Python's os.cpu_count, nproc; the load first from the cores
# busy at normal priority that the loop's games here last published (home.published_split, while under 30 s old: the
# games run at nice 19 and take only what dev work leaves, so they must not hold tests back, 2026-09-27, nor the
# headless workers' commands at home.WORKER_NICE, 28 Sep), else measured here over 2 s (home.measure_now, REFLECT
# 2026-09-28 20:52 F11: with none published the total load, mostly niced loop work, held tests for hours), then,
# only where neither can be read (a coder's sandbox refusing ps), the 1-minute load from sysctl vm.loadavg, Python's
# os.getloadavg, `uptime` (macOS "load averages: 7.21 ...", Linux "load average: 1.00, ...") and /proc/loadavg, which
# counts the niced games and workers too. While it waits, loop/evalline.py's agents line names it and how long
# (evalline.gate_waits, from ps).
if [ $# -eq 0 ]; then
    echo "usage: [GATED_MAX_WAIT=<seconds>] tools/gated_test.sh <command> [args...]" >&2
    exit 2
fi

loop_dir="$(cd "$(dirname "$0")/../loop" && pwd)"
fraction="${BC_LOOP_HOME:-$HOME/.bc-loop}/load-fraction"
# The share, read again at every check, so a load-fraction changed meanwhile applies to tests already waiting.
read_share() {
    share="$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); from home import load_fraction_copy, load_share
try:
    text = open(sys.argv[2]).read()
except PermissionError:  # a coder sandbox: the copy the upkeep keeps for it, as native.load_fraction reads
    try:
        text = load_fraction_copy().read_text()
    except OSError:
        text = ""
except OSError:
    text = ""
print(load_share(text))' "$loop_dir" "$fraction" 2>/dev/null)" || share=0.7
    [ -n "$share" ] || share=0.7
    # BC_GATE_URGENT=1: only for work the evaluation waits on (a merge turn's tests, set by driver.lane_env in that
    # session's environment), which gates at the hard stop, load >= cores, not the share: E0366's merge turn resolved
    # its conflict in 5 min on 29 Sep, then its bot tests waited ~30 min at load 12-19 behind builders' tests while the
    # evaluation lock was free and the hosts idle. Nothing else sets it.
    if [ "${BC_GATE_URGENT:-}" = 1 ]; then
        share=1
    fi
}

# Prints the output of the first of the named functions that prints a plain number, or nothing.
first_number() {
    local source value
    for source in "$@"; do
        value="$("$source" 2>/dev/null)"
        if [[ "$value" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
            echo "$value"
            return
        fi
    done
}
cores_sysctl() { sysctl -n hw.ncpu; }
cores_getconf() { getconf _NPROCESSORS_ONLN; }
cores_python() { python3 -c 'import os; print(os.cpu_count())'; }
cores_nproc() { nproc; }
load_dev() {
    python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); from home import measure_now, published_split
split = published_split() or measure_now()
print(round(split.dev, 2) if split else "")' "$loop_dir"
}
load_sysctl() { sysctl -n vm.loadavg | awk '{print $2}'; }
load_python() { python3 -c 'import os; print(os.getloadavg()[0])'; }
load_uptime() { uptime | sed -nE 's/.*load averages?: *([0-9]+([.,][0-9]+)?).*/\1/p' | tr , .; }
load_proc() { cut -d' ' -f1 /proc/loadavg; }
cores() { first_number cores_sysctl cores_getconf cores_python cores_nproc; }
load() { first_number load_dev load_sysctl load_python load_uptime load_proc; }
# True only for a numeric load and core count with load < share x cores. Measures once a check (a measured split
# takes 2 s) and keeps what it judged in judged_load and judged_cores, which the messages below print.
under_gate() {
    read_share
    judged_load="$(load)"
    judged_cores="$(cores)"
    awk -v l="$judged_load" -v c="$judged_cores" -v s="$share" 'BEGIN {
        n = "^[0-9]+([.][0-9]+)?$"
        exit !(l ~ n && c ~ n && c > 0 && l + 0 < s * c)
    }'
}

max_wait="${GATED_MAX_WAIT:-}"
if [ -n "$max_wait" ] && ! [[ "$max_wait" =~ ^[0-9]+$ ]]; then
    echo "gated_test: GATED_MAX_WAIT must be whole seconds, not '$max_wait'" >&2
    exit 2
fi
waited=0
if ! under_gate; then
    echo "gated_test: load '$judged_load' is not under $share x $judged_cores cores; waiting (checking every 20 s)"
    until under_gate; do
        if [ -n "$max_wait" ] && [ "$waited" -ge "$max_wait" ]; then
            echo "gated_test: gave up after ${waited} s (GATED_MAX_WAIT=$max_wait); load '$judged_load', not run: $*" >&2
            exit 75
        fi
        sleep 20
        waited=$((waited + 20))
        if [ $((waited % 300)) -eq 0 ]; then
            echo "gated_test: still waiting at $(date +%H:%M:%S): load '$judged_load', gate $share x $judged_cores cores"
        fi
    done
fi
echo "gated_test: load $judged_load under $share x $judged_cores cores at $(date +%H:%M:%S), running: $*"
"$@"
