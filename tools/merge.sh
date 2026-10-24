#!/bin/bash
# The one route for code onto main: merges the orchestrator's branch into main, tests the merge, and pushes it, stopping
# at the first failure (REFLECT 0647 F2: a failed commit chained with ';' let main merge a branch without its fixes). A
# direct change goes on a branch first and through here too: a commit tested after it lands on local main is one the
# referee refresh can already read. Run from any checkout of the repo; main must be checked out in the main one:
#
#     tools/merge.sh <branch> "<message>" test_health tools.test_my_messages referee/test_run
#
# Loop modules run from loop/, referee/ ones as referee/<name>, tools and mapgen ones as tools.<name> and
# mapgen.<name> (from the repo root); a path (tools/test_hooks.py) names its module too, and a bare name only in one of
# tools/, referee/ or mapgen/ resolves there (resolve). Every named module must exist in the merge, or it stops before
# any test, naming them (a wrong name used to fail only at the end, as an import error). test_home, test_launches
# and test_online (its online-reader tripwire scans every loop file; owed-reconcile broke it on main, 29 Sep) always run
# (repo-wide rules). Also run: every loop/ and tools/ test module the push changes, the test module of each loop/ and tools/ module it
# changes, every test module importing one, and the test module of every file importing one (a hub's importers aside:
# HUB_IMPORTERS below), over origin/main..<the merge>, so an
# unpushed commit already on local main is tested too (a3adf05 changed meta's proposal checks; test_maintenance, not
# listed, then failed on main). The merge is made and tested in a detached worktree; main moves (fast-forward) only
# after the tests pass, so the loop (whose referee refresh reads local main) never sees an untested merge (2026-09-29
# 00:38: a merge committed to main before its tests, then rolled back, left the referee on a commit off main and
# E0280's guard stopped the loop). Only then does it write the pass marker tools/hooks/pre-push looks for,
# <git common dir>/test-passed/<the merge>, holding the time, branch and modules run (about 100 bytes, a week's kept),
# and push.
set -euo pipefail
[ $# -ge 2 ] || { echo "usage: tools/merge.sh <branch> \"<message>\" <test module>..." >&2; exit 2; }
common=$(git -C "$(dirname "$0")" rev-parse --path-format=absolute --git-common-dir)
repo=$(dirname "$common")
branch=$1; msg=$2; shift 2
cd "$repo"
[ "$(git symbolic-ref -q --short HEAD)" = main ] || { echo "merge.sh: $repo is not on main" >&2; exit 1; }
# sig's own uncommitted edits in the main checkout (research notes) don't block a merge that leaves those files alone;
# the fast-forward below would refuse to overwrite them anyway, and a merge touching one stops here.
dirty=$(git diff --name-only HEAD)
wt=$(mktemp -d "${TMPDIR:-/tmp}/merge-wt.XXXXXX")
git worktree add -q --detach "$wt" main
trap 'git -C "$repo" worktree remove --force "$wt" 2>/dev/null || true' EXIT
cd "$wt"
ln -s "$repo/.venv" .venv
git merge --no-ff "$branch" -m "$msg"
if [ -n "$dirty" ]; then
  clash=$(comm -12 <(echo "$dirty" | sort) <(git diff --name-only main HEAD | sort))
  [ -z "$clash" ] || { echo "main has uncommitted changes to files this merge changes:" $clash >&2; exit 1; }
fi
# A named module in the form run_one takes (a class or test after it kept), or nothing when no file holds it.
resolve() {
  local m=$1 name dirs
  case $m in
    loop/*.py) m=${m#loop/}; m=${m%.py};;
    referee/*.py) m=${m%.py};;
    */*.py) m=${m%.py}; m=${m//\//.};;
  esac
  case $m in
    referee/*) name=${m#referee/}; [ -f "referee/${name%%.*}.py" ] && echo "$m";;
    tools.*|mapgen.*) name=${m#*.}; [ -f "${m%%.*}/${name%%.*}.py" ] && echo "$m";;
    *) if [ -f "loop/${m%%.*}.py" ]; then echo "$m"; else
         dirs=$(for d in tools referee mapgen; do [ -f "$d/${m%%.*}.py" ] && echo "$d"; done)
         case $dirs in
           referee) echo "referee/$m";;
           tools|mapgen) echo "$dirs.$m";;
         esac
       fi;;
  esac
}
named=""; missing=""
for m in "$@"; do
  if found=$(resolve "$m") && [ -n "$found" ]; then named="$named $found"; else missing="$missing $m"; fi
done
[ -z "$missing" ] || { echo "merge.sh: no such test module in the merge, or one in two of tools/, referee/, mapgen/:$missing (name loop ones bare, others referee/<name>, tools.<name>, mapgen.<name>, or give a path); main not moved" >&2; exit 2; }
base=$(git merge-base origin/main HEAD 2>/dev/null || git hash-object -t tree /dev/null)  # no origin/main: all of it
changed=$(git diff --name-only "$base" HEAD)
# A loop/ or tools/ file's test module in the form run_one takes (test_x, tools.test_x), or nothing when it has none.
test_of() {
  local dir=${1%%/*} name=${1##*/}; name=${name%.py}
  case $name in test_*) ;; *) name=test_$name;; esac
  [ -f "$dir/$name.py" ] || return 0
  if [ "$dir" = loop ]; then echo "$name"; else echo "tools.$name"; fi
}
# The loop/ and tools/ files importing module $1 (both put loop/ on the path, so a name means either folder's file).
importers() {
  grep -lE "^[[:space:]]*(import|from) ([a-z0-9_]+, )*$1([^a-z0-9_]|$)" loop/*.py tools/*.py 2>/dev/null || true
}
# Every changed loop/ and tools/ module's own test module, every test module importing it, and the test modules of the
# files importing it, a reverse-import scan one level deep (473fd77d changed tools/watch.py and broke
# tools/test_blunders, blunders importing watch, unseen until a night later). Bounded: a module more than HUB_IMPORTERS
# files import (meta, driver, online) is a hub whose importers' tests are left out, only its direct test importers
# run, and each is named, so a change to it doesn't run everything.
HUB_IMPORTERS=8
extra=""; hubs=""
for file in $(echo "$changed" | grep -E '^(loop|tools)/[a-z0-9_]+\.py$' || true); do
  name=${file##*/}; name=${name%.py}
  [ -f "$file" ] && extra="$extra $(test_of "$file")"
  case $name in test_*) continue;; esac
  users=$(importers "$name")
  plain=$(echo "$users" | grep -v '/test_' || true)
  for user in $(echo "$users" | grep '/test_' || true); do extra="$extra $(test_of "$user")"; done
  if [ "$(echo "$plain" | grep -c . || true)" -gt "$HUB_IMPORTERS" ]; then hubs="$hubs $name"; continue; fi
  for user in $plain; do extra="$extra $(test_of "$user")"; done
done
[ -z "$hubs" ] || echo "merge.sh: hub modules (over $HUB_IMPORTERS importers), only their direct test importers run:$hubs"
mods=$(printf '%s\n' $named $extra test_home test_launches $([ -f loop/test_online.py ] && echo test_online) | awk 'NF && !seen[$0]++')
echo "merge.sh: testing $(echo $mods | wc -w | tr -d ' ') modules: $(echo $mods)"
# Runs the modules 4 at a time (each through gated_test.sh, which still waits for the load share); a failed module
# prints its log and stops the merge before the push, as does one gated_test.sh gave up on without running (its exit
# 75, under an inherited GATED_MAX_WAIT), reported "NOT RUN (load)", never a pass.
# A module with no unittest.TestCase (mapgen/test_gen.py: plain functions its __main__ block calls, printing "ok") runs
# as `python -m <module>`, its exit 0 a pass: unittest finds no tests there and fails it (portal-bets' merge, 29 Sep
# 18:19).
logs=$(mktemp -d "${TMPDIR:-/tmp}/merge-tests.XXXXXX")
# Module $1's file (run_one's form).
file_of() {
  local file
  case $1 in
    referee/*) file=${1#referee/}; echo "referee/${file%%.*}.py";;
    tools.*|mapgen.*) file=${1#*.}; echo "${1%%.*}/${file%%.*}.py";;
    *) echo "loop/${1%%.*}.py";;
  esac
}
run_one() {
  local how=unittest
  grep -q "TestCase" "$(file_of "$1")" || how=script
  case $how:$1 in
    unittest:referee/*) (cd referee && PYTHONPATH=.:../loop ../tools/gated_test.sh ../.venv/bin/python -m unittest "${1#referee/}");;
    script:referee/*) (cd referee && PYTHONPATH=.:../loop ../tools/gated_test.sh ../.venv/bin/python -m "${1#referee/}");;
    unittest:tools.*|unittest:mapgen.*) PYTHONPATH=loop tools/gated_test.sh .venv/bin/python -m unittest "$1";;
    script:tools.*|script:mapgen.*) PYTHONPATH=loop tools/gated_test.sh .venv/bin/python -m "$1";;
    unittest:*) (cd loop && PYTHONPATH=. ../tools/gated_test.sh ../.venv/bin/python -m unittest "$1");;
    *) (cd loop && PYTHONPATH=. ../tools/gated_test.sh ../.venv/bin/python -m "$1");;
  esac
}
# Whether this merge changes module $1's test file or the file it tests (test_x.py's x.py).
touched() {
  local file; file=$(file_of "$1")
  git diff --name-only HEAD^1 HEAD | grep -qxF -e "$file" -e "$(dirname "$file")/$(basename "$file" | sed 's/^test_//')"
}
# Records module $1's exit status $2 that isn't a pass: a failure, or 75, not run (gated_test.sh gave up on the load).
said() {
  echo "$1" >> "$logs/failed"
  if [ "$2" -eq 75 ]; then echo "$1" >> "$logs/notrun"; echo "NOT RUN (load) $1"; else echo "$1" >> "$logs/broke"; echo "FAIL $1"; fi
}
for m in $mods; do
  while [ "$(jobs -rp | wc -l)" -ge 4 ]; do sleep 1; done
  ( if run_one "$m" > "$logs/${m//\//_}.log" 2>&1; then echo "ok   $m: $(grep -E '^Ran ' "$logs/${m//\//_}.log" | tail -1 || echo "run as a script, exit 0")"; else said "$m" $?; fi ) &
done
wait
# A module failing beside others reruns once on its own (timing tests such as test_baselines' stage overlap fail under
# the parallel load, REFLECT 0039 F22); it fails the merge only if it fails alone too, and the rerun is named.
if [ -s "$logs/failed" ]; then
  mv "$logs/failed" "$logs/first"
  rm -f "$logs/notrun" "$logs/broke"
  for m in $(cat "$logs/first"); do
    if run_one "$m" > "$logs/${m//\//_}.log" 2>&1; then
      echo "ok   $m on its own rerun (failed beside the others)"
      # A pass only when alone is a defect, not a success (REFLECT 06:11 F15): record it for a fix, and as a row of
      # <loop home>/merge-reruns.jsonl (time, module, branch; the last 200 kept), which health.merge_reruns alerts
      # on at 2+ in a day (REFLECT 2026-09-29 17:32 F20).
      echo "- ($(date '+%d %b %H:%M')) merge.sh: $m passed only when rerun alone while merging $branch (order- or load-dependent test); fix it. Waits on the next builder round: a flaky test is batched with the next loop fix" >> "${BC_LOOP_HOME:-$HOME/.bc-loop}/owed.md"
      reruns="${BC_LOOP_HOME:-$HOME/.bc-loop}/merge-reruns.jsonl"
      .venv/bin/python -c 'import json, sys, time; print(json.dumps({"at": round(time.time()), "module": sys.argv[1], "branch": sys.argv[2]}))' "$m" "$branch" >> "$reruns"
      if [ "$(wc -l < "$reruns")" -gt 200 ]; then tail -200 "$reruns" > "$reruns.tmp" && mv "$reruns.tmp" "$reruns"; fi
    else said "$m" $?; fi
  done
fi
# A subprocess timeout in a module this merge doesn't touch is the load's, not the branch's (REFLECT 2026-10-01 03:17:
# test_mapideas timed out after 931 s at load ~40 and failed an unrelated merge): it gets one more run before failing.
if [ -s "$logs/broke" ]; then
  for m in $(cat "$logs/broke"); do
    grep -q "TimeoutExpired" "$logs/${m//\//_}.log" && ! touched "$m" || continue
    echo "merge.sh: $m timed out (TimeoutExpired) and this merge doesn't touch it: retrying it once"
    if run_one "$m" > "$logs/${m//\//_}.log" 2>&1; then
      echo "ok   $m on its timeout retry"
      grep -vxF "$m" "$logs/failed" > "$logs/f.tmp" || true; mv "$logs/f.tmp" "$logs/failed"
      grep -vxF "$m" "$logs/broke" > "$logs/b.tmp" || true; mv "$logs/b.tmp" "$logs/broke"
    fi
  done
fi
if [ -s "$logs/failed" ]; then
  for m in $(cat "$logs/failed"); do echo "=== $m"; tail -40 "$logs/${m//\//_}.log"; done
  broke=$(cat "$logs/broke" 2>/dev/null | tr '\n' ' ' || true); notrun=$(cat "$logs/notrun" 2>/dev/null | tr '\n' ' ' || true)
  echo "merge.sh: ${broke:+failed modules: $broke; }${notrun:+not run (load): $notrun; }not pushed" >&2; exit 1
fi
.venv/bin/python tools/doc_size.py > /dev/null
merged=$(git rev-parse HEAD)
mkdir -p "$common/test-passed"
find "$common/test-passed" -type f -mtime +7 -delete
echo "$(date '+%Y-%m-%d %H:%M:%S') merge.sh $branch: $(echo $mods)" > "$common/test-passed/$merged"
cd "$repo"
git merge -q --ff-only "$merged"
git push -q origin main
git log --oneline -1
