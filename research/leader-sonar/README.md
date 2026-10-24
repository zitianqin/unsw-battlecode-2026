# The leaders' sonar messages

A study of 149 public replays of the top five teams (`~/.bc-loop/study-replays`, 2026-09-24; ranks below are as of
that day), kept so the formats can be decoded or reused later. Every value hitting one of our dragons reaches our bot through
`get_sonar_messages()`, with no sender attached. `decode.py` has the two decoders; `extract.py` is the script that
pulled every ping with the board state around it (it writes a pickle of a few GB; delete it afterwards), and
`exposure.txt` has per-game counts of each team's messages that hit enemy dragons.

Read full 64-bit values from `value64` in `tools/replay.capnp`; the old 32-bit `value` field is 0 in these replays.

## SSS (team 91, rank 1): plain position reports, decoded

64-bit, high to low: magic `0xb3c` (12 bits) | dragon id (14) | length (7) | x (6) | y (6) | round (9) | check (10).

- 99.3% of pings carry the magic; the rest are one constant, `0x53a11c0d`, meaning unknown.
- Of 59,700 distinct messages, 99.97% match the named dragon's head and length at the stated round (46,197 the
  head before its move, 11,931 after). The id is always the sender's own team.
- They flood reports: 92% of pings relay a teammate's report rather than the sender's own.
- The check bits are one fixed value per payload but not mod-prime, xor-fold, CRC32 or a Fibonacci hash; the
  function wasn't recovered. Forging their messages would need it.
- Same format from match 121802 to 137777.
- Reaching enemies: median 195 messages a game (mean 3,390; Big Empty 23-36k). 96% describe an SSS dragon
  outside the receiver's 7x7 view; median age 0-6 rounds, 37% at most 2 rounds old; about 1-6 distinct SSS
  dragons a round.
- Example (game 137771): `0xb3c026823eca5ae1` is dragon 154, length 4, at (31,25) in round 150, as in the replay;
  dragon 11 relayed it in round 152 and it hit enemy dragon 183.

## Sabotage-d (team 46, rank 2): position reports from round 350, decoded

32-bit, high to low: x (6) | y (6) | length (8) | flags (4) | low 8 bits (change with the flags; probably a check,
not decoded). Flag bit 0 is a sighting of an enemy of theirs; flag bits 1-2 count relay hops (about a round of
lag each).

- Only 15 of 41 games have pings, all from round 350; the silent games are on small maps.
- 97% of fresh (hop 0) own reports match the sender's head exactly.
- Reaching enemies: median 0 a game, mean 311, up to 2.7k on Big Empty. 70% place a Sabotage-d dragon, 96% of them
  outside the receiver's view; 30% are sightings of the receiver's own team.
- Example (game 126934): `0x8a020422` is (34,32), length 32, 2 relay hops: dragon 209's head in round 350; dragon 35
  relayed it and it hit enemy dragon 495.

## 龙虎豹 (team 470, rank 3): not decodable

Exactly 4 different 64-bit values per dragon every turn; every bit, and every bit of the XOR of two messages, is set
half the time. Never relays. Two duplicate games gave identical values, so they come from game state, not a clock.
Unrelated dragons sometimes send the same value in the same round (24k pairs): probably a keyed hash or cipher of
shared content, possibly deliberate noise. Median 112 reach enemies a game, up to 15k.

## What this is worth

None of them sends pearls or targets: decoding would only tell us where some of their dragons are, 4+ tiles away
and often a few rounds old, and it depends on their formats surviving to the tournaments. Still not built as of
2026-09-26; the backlog has I175 (replay or forge their formats) and I142 (recognise the opponent from its sonar),
both todo. If it's tried: filter SSS by the magic, add the reports to an "enemy heads seen" map with an age, and
put it through the SPRT; expect a gain near zero. Echoing captured SSS reports back at them needs no key, but
their reports carry a round, so they can likely discard stale ones. Unknown: both check functions, `0x53a11c0d`,
and whether 470's values carry content.
