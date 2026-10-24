#!/bin/bash
# Usage: bash tools/orch/claim-line.sh <lane output file>, right after starting `driver.py --once` as a background task
# (the file is the one the harness names for it). Waits up to 90 s for the driver's claim outcome and prints it, so a
# reply quotes what the lane did instead of guessing (REFLECT 2026-09-30 09:12 F10, MISSES 26: "That lane is started",
# then "No lane was free after all"): "lane N: E#### starts I### ..." (claimed), "lane N: maintenance/claim deferred:
# ..." (with the in-flight count), "lane N: no idea claimed", "lane N: stopped before claiming an idea", or the usage
# pause ("lane N's next claim waits for the new 5 h window"). Then the --once drivers running now. What the next claim
# should take comes from `loop/evalline.py --queue`, not from here. Exits 1 if no such line came in 90 s.
file=${1:?usage: claim-line.sh <lane output file>}
outcome='lane [0-9]+: E[0-9]+ starts |lane [0-9]+: maintenance/claim deferred|lane [0-9]+: no idea claimed|lane [0-9]+: stopped before claiming|lane [0-9]+.s next claim waits'
for _ in $(seq 90); do
  if line=$(grep -E -m1 "$outcome" "$file" 2>/dev/null); then
    echo "$line"
    # The Python processes only: each lane's shell and uv wrapper name the command too.
    echo "--once drivers running: $(pgrep -f 'python3 [^ ]*loop/driver\.py --once' | wc -l | tr -d ' ')"
    exit 0
  fi
  sleep 1
done
echo "claim-line.sh: no claim line in $file after 90 s; its last line: $(grep -v '^\s*$' "$file" 2>/dev/null | tail -1)" >&2
exit 1
