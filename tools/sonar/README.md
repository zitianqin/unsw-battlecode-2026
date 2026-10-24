# Sonar formats: find, verify and table every team's protocol

The bot's passive listener (`listen.hpp` in the bot folder, from the branch `draft/listen`) recognises the enemy's
team from the values it hears and decodes the ones that carry positions, using the table `listen_formats.hpp` beside
it. That table is
generated from `spec.toml` (one entry per team per protocol version) and `measured.json` (how each entry did on
replays). These tools rebuild both from the newest replays. Run everything from the repo root with the repo's venv;
`--bot` is a bot folder that has `listen.hpp` (a `draft/listen` checkout, or main's `bot/` once it is merged).

## Before a tournament: one command

```sh
.venv/bin/python tools/sonar/refresh.py --bot <bot folder> --since 2026-10-10T08:00 --fetch
```

It (1) fetches each team's newest ranked battles (`--battles 2` each, `--teams ...` to limit; about 3 s a battle,
1 request a second), (2) checks every entry on the games since `--since` and prints the teams whose values no trusted
entry covers (new or changed formats), (3) runs the field search on each and appends its entries to `spec.toml`
(`replays-<day>`, `replays-<day>-tag`), (4) re-measures every entry on all games into `measured.json` and writes
`<bot>/listen_formats.hpp`. Add `--dry-run` to see step 3's entries without writing anything. Then run the bot tests
(`tools/gated_test.sh .venv/bin/python loop/bot_tests.py --bot <bot folder> --tests <its tests/bot>`): their
round-trip case covers every new row. Commit `spec.toml`, `measured.json` and the header.

## The steps by hand

1. Fetch replays: `.venv/bin/python tools/replays.py fetch --team <id> --battles 3 --ranked` (into `data/replays`,
   indexed in `data/replays/index.jsonl`). The tools also read the loop's `~/.bc-loop/study-replays`,
   `scout-replays` and our ranked online games.
2. Verify every entry: `.venv/bin/python tools/sonar/verify.py --bot <bot folder> [--since ISO] [--team ...]
   --out report.md [--update]`. The report gives per entry its cover, head accuracy, stray (and the team it is
   confused with), per team the game groups each entry covers and those none does, and a stream check (games whose
   values lock the bot's detector onto the right team, a wrong one, none). `--update` writes `measured.json`.
3. Discover a changed team: `.venv/bin/python tools/sonar/discover.py --team <id> --since ISO --version
   replays-<day> [--append]` prints (or appends) its entries.
4. Merge a research report: `.venv/bin/python tools/sonar/reports.py research/<report>.md --version
   report-<day> [--append]` turns its Master Protocol Reference Table into entries (as stated; verify.py judges them).
5. Write the table: `.venv/bin/python tools/sonar/formats.py --bot <bot folder>` (`--check` fails if stale).

Hand-found formats (a field XORed with a constant: `flip`; a sighting of our dragons: `sighting = true`) are spec
edits; `spec.toml`'s header shows every key. Never delete an old version: a team may bring any of them back.

## What the measurements mean

- cover: the share of the team's values the entry recognises, in the best game group (a submission, or a day).
- head: of the recognised values, the share whose decoded head is a real dragon head of that team (of its enemy, for
  a sighting) within a round of the round it states. An entry is `verified` at 0.95 or more on 50 values; only
  verified entries' facts are stored by the bot.
- stray: the largest share of one other team's values, in one game, the entry recognises. An entry `detects` at
  cover 0.3 or more and at least 4 times its stray; only detecting entries take part in the bot's detection.
- Games come from ranked play (Cache me outside, 952: unranked too), with `loop/decoys.py`'s flagged versions left
  out. The cache of extracted games (`--cache`, default `$TMPDIR/bc-sonar-cache`, about 0.2 MB a game) is rebuilt from
  the replays when missing.

## Tests

`.venv/bin/python -m unittest tools.sonar.test_sonar`: the spec round-trips and refuses an overlapping field, the
field search finds a planted layout, a report row becomes its entry.
