# tools/keyed: rerun the keyed-sonar tests on new replays

These tools check what a team's sonar values are: noise, a hash of the sender's state, a shared codebook, or a
plaintext bitfield we can decode. They also say whether the format changed since the last analysis. The findings of
1 Oct 2026 are in `~/.bc-loop/reports/keyed-sonar-2026-10-01.md`; the scripts it came from are on branch
`research/keyed-sonar`.

Everything they write goes to `~/.bc-loop/keyed/` (set `KEYED_DIR` to change it), never into git: fetched replays,
the index, per-team pickles (a few hundred MB each) and results. Every script is one process. Run each under
`nice -n 20` (as below) and one at a time. On this Mac, check load first (`tools/devload.sh`) and don't start
while the one-minute load is at or above the core count. Use ranked games only; each script filters them itself.

Run from the repo root with the main checkout's venv: `PY=/Users/me/Dev/unsw-battlecode-2026/.venv/bin/python`.
`SINCE` is an ISO time such as `2026-10-01T00:00Z`. A naive time counts as UTC.

## Steps, in order, per team

```sh
# 0. Keep the last result to compare against (skip on the first run).
cp ~/.bc-loop/keyed/battery/$TEAM.json /tmp/$TEAM-before.json

# 1. Fetch the team's ranked games since SINCE (public site pages; cached games are skipped, so reruns are cheap).
nice -n 20 $PY tools/keyed/fetch.py $TEAM --since $SINCE

# 2. Rebuild the index over every replay folder we hold (fetched, data/replays, study, ladder, our online games).
nice -n 20 $PY tools/keyed/index.py

# 3. Extract the team's pings with game state (walks every event; minutes per 100 games).
nice -n 20 $PY tools/keyed/extract.py $TEAM --since $SINCE

# 4. The battery, per version (submission id, else 20-game blocks), with the decoy check (loop/decoys.py).
nice -n 20 $PY tools/keyed/battery.py $TEAM --since $SINCE > /dev/null
nice -n 20 $PY tools/keyed/show.py $TEAM

# 5. Did the format change? Between versions, and against the kept copy from step 0.
nice -n 20 $PY tools/keyed/changed.py $TEAM --baseline /tmp/$TEAM-before.json

# 6. The specific tests (each prints its own reading; see the docstrings for what 1 Oct gave):
nice -n 20 $PY tools/keyed/statematch.py $TEAM --since $SINCE     # cross-game state match: which fields decide the value
nice -n 20 $PY tools/keyed/recur.py $TEAM --since $SINCE --save   # recurrence table, held-out recognition, send schedule
nice -n 20 $PY tools/keyed/fieldfind.py $TEAM --since $SINCE --fit 3 --round-key   # plaintext / XOR-keyed fields
nice -n 20 $PY tools/keyed/tags.py $TEAM --since $SINCE           # tags, constant bits, relays, payload lifetime
nice -n 20 $PY tools/keyed/mixers.py $TEAM --since $SINCE         # inverse of common unkeyed 64-bit mixers
```

Steps 1-2 are shared: to do several teams, run step 1 for each team, then step 2 once, then steps 3-6 per team.

## Reading the output

- **battery / show**: `ss` is same-state pairs and the share with equal values (near 1 means the value is a function
  of state, not noise). `pop` is the XOR popcount between different states (32 means fully scrambled, well below 32
  means a plaintext bitfield). `2rounds` counts values reused in another round (0 means the round is an input).
  `shared` is the share of turns whose value another dragon sends the same round. `gf` lists value bits that are
  GF(2)-linear in the state. `mix` is the best bias after inverting a common mixer (0.5 means a constant bit, which
  says nothing about the mixer).
- **changed**: prints `FORMAT CHANGED` where a version's signature differs from the one before it or from the
  baseline. The signature covers rays, values per turn, tag, constant bits, round reuse, sharing and determinism.
  After a change, rerun step 6 on games since the change and replace that team's decoder in the report.
- **fieldfind**: a field whose held-out share is 0.95 or more is decoded, and the printed formula is the decoder,
  e.g. 545: `((v >> 47) & 0x1ff) ^ round == 0x83`. A share near 0.5 with a clean formula means one message type
  among several.
- **recur --save** writes `~/.bc-loop/keyed/recur/<team>.json` ({round: [values seen in 2+ games]}): a recognition
  table for teams like 801 whose values recur across games.

## Checked on

1 Oct 2026, 04:30 AEST, on current data: fetch (249, 3 new games), index (14,390 matches), extract/battery/show/
changed/statematch/recur/tags/fieldfind on 249 since 29 Sep, and fieldfind on 545. The 545 run reproduces its round
field (fit 1.000, held-out 1.000, n 493).
