#!/bin/bash
# Usage: bash tools/orch/watch.sh, as a background task (no redirection); re-arm after each return.
# Waits for alert lines worth the orchestrator's attention (sig, 2026-09-27: wake this session less).
# Noise is dropped. An urgent line returns at once; anything else is batched, returning at most every 15 minutes.
home=${BC_LOOP_HOME:-~/.bc-loop}  # set by tests only (loop/home.py)
f=$home/host-alerts.log
# Refuse to run where nobody reads its output: sent to /dev/null (twice on 2026-09-28, REFLECT 0647 F1) or detached
# with & from a Bash call that then ended (30 Sep 03:49 and 07:15, REFLECT 0912 F3), it would wait and then report
# nothing. Only a background task's output file (or a terminal) counts. A refusal can't count on its own stderr (both
# detached starts sent that to /dev/null too), so it also logs an urgent WATCHER REFUSED alert, which the next armed
# watcher prints at once.
refuse() {
  echo "watch.sh: $1; start it as a background task (run_in_background) with no redirection and no &" >&2
  echo "$(date '+%Y-%m-%d %H:%M:%S') WATCHER REFUSED: watch.sh (pid $$) $1; re-arm it as a background task" >> "$f"
  exit 2
}
[ /dev/stdout -ef /dev/null ] && refuse "stdout is /dev/null"
# Run from a private copy, so editing this file never changes a running watcher (bash reads scripts as it runs;
# REFLECT 2052 F13: twice on 2026-09-28).
if [ -z "${WATCH_COPY:-}" ]; then
  copy=$(mktemp "${TMPDIR:-/tmp}/watch.XXXXXX") && cp "$0" "$copy" && WATCH_COPY=1 WATCH_SRC="$0" exec bash "$copy" "$@"
fi
repo=$(cd "$(dirname "${WATCH_SRC:-$0}")/../.." && pwd)
noise='playing again|failing: .*exit 255|failing: .*timed out|predicted games off|rested [0-9]+s|played 0 of [0-9]+ games in the latest test|map layouts now recorded for'
urgent='WATCHER REFUSED|USAGE|LANES SHORT|RESULT:|PROMOTED|NEW CHAMPION|PUBLISHED|is kept without|is promising without|WARNING online|lost or drew|DISCORD DIGEST|REFLECT|meta applied|meta rejected|METHOD NOTES|audit applied|ADVMAPS|stuck|DOC REFS|backlog rows|official-map|health reminder due|RISERS|JUDGED|BACKLOG|backlog audit|-backlog.md'
count() { grep -v -e '^\s*$' "$f" 2>/dev/null | wc -l; }
mark=$home/.watch-printed  # the line count this watcher last printed up to: a lapse or re-arm never loses alerts
start=$(cat "$mark" 2>/dev/null || count); first=""
[ "$start" -gt "$(count)" ] && start=$(count)  # the log was rotated or trimmed
champ() { git -C "$repo" rev-parse -q --verify loop/champion 2>/dev/null; }
was=$(champ)  # a promotion logs PROMOTED only to its lane's log, not host-alerts.log
while :; do
  sleep 30
  # Orphaned (parent 1): the Bash call that started it with & has ended, so nothing reads its output file any more.
  [ "$(ps -o ppid= -p $$ | tr -d ' ')" = 1 ] && refuse "was detached (the shell that started it has ended)"
  now=$(champ)
  if [ -n "$was" ] && [ "$now" != "$was" ]; then new="NEW CHAMPION: loop/champion ${was:0:7} -> ${now:0:7} ($(git -C "$repo" describe --tags --exact-match $now 2>/dev/null))"; upto=$start; break; fi  # batched lines reprint next start
  upto=$(count)
  new=$(grep -v -e '^\s*$' "$f" | sed -n "$((start + 1)),${upto}p" | grep -Ev "$noise")
  [ -z "$new" ] && continue
  echo "$new" | grep -Eq "$urgent" && break
  [ -z "$first" ] && first=$(date +%s)
  [ $(( $(date +%s) - first )) -ge 900 ] && break
done
echo "$new"
# The usage levels the loop acts on itself (loop/quota.py); what is left is the orchestrator's own work.
echo "$new" | grep -q 'USAGE 85%' && echo "USAGE 85%: write your handoff notes now, as one block (CLAUDE.md: before a compaction)"
echo "$new" | grep -q 'USAGE 95%' && echo "USAGE 95%: the loop paused itself (no claims, coders stopped until the reset); pause your own builders, fix rounds and reviews too, and write handoff notes if not done"
echo "${upto:-$start}" > "$mark"
cd "$repo" && .venv/bin/python loop/evalline.py
echo "RE-ARM THE WATCHER"
