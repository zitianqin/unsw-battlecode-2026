#!/bin/bash
# Usage: wake.sh 'YYYY-MM-DD HH:MM'. Sleeps until then, prints the resume checklist, the eval line and the lane count.
cd "$(dirname "$0")/../.."
w=$(( $(date -j -f '%Y-%m-%d %H:%M' "$1" +%s) - $(date +%s) )); [ $w -gt 0 ] && sleep $w
date +%H:%M
echo "WAKE $1 (sig: 5-hourly wakes in case a usage limit stopped the session): (1) list every agent, shell, alert, reminder and lane that finished or failed since the last reply (task outputs, ~/.bc-loop/host-alerts.log, results.jsonl), judge each; (2) rerun owed driver.py --turn / --judge (ps first, never twice); (3) refill to driver.IN_FLIGHT in flight with --once; (4) re-arm watch.sh; (5) resume owed.md held work; (6) tell sig what was missed and done"
.venv/bin/python loop/evalline.py
pgrep -f "python3 loop/driver.py --once" | wc -l
