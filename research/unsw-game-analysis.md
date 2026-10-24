# UNSW Battlecode 2026: game analysis

Written 2026-09-23. Sources: the docs mirror in [`docs/unsw/`](../docs/unsw/README.md), the public engine source
([unswcpmsoc/unswbc @ b21fa68](https://github.com/unswcpmsoc/unswbc/tree/b21fa68699d46d12d13705304dc8f66701ba0939)),
the toolkit wheel `unswbc==0.3.13`, the live site, and local test runs. Anything labelled **hypothesis** has not
been tested yet. Updated 2026-09-26 where the rules have changed since (protocol 3 sonar, per-match pearl seeds,
toolkit 1.1.0); the replay statistics, ladder snapshot and timings are still the 2026-09-23 snapshot.

## TL;DR

1. **The contest is short.** Sprint tournament **1 Oct** (8 days away), Qualifiers **10 Oct**, Grand Final
   **17 Oct** in person at UNSW. The ladder opened 21 Sep. Seeding uses ladder rating.
2. **Write the bot in C++.** The judge meters CPU "points" in a WebAssembly sandbox: 100M per dragon per turn. A
   32×32 flood fill costs ~19M points in Python and ~0.3M in C++, roughly 60× cheaper. 223 of the 329 ladder teams
   use C++, and so do 9 of the top 10.
3. **Pearl timing could be predicted until 2026-09-25; no longer.** **Superseded 2026-09-25:** the organisers announced that from 17:00 AEST that day pearl schedules are seeded randomly per match (the seed is written into each replay), because 20+ teams had hardcoded per-map schedules; a new judge and CLI follow. What follows was true before then.
   The engine's pearl RNG used a fixed seed
   (`PEARL_RNG_SEED = 0x5eed5eed`), and the countdown schedule never depends on what bots do. On Trophy, a
   ladder replay (match 74273) and a local run matched on **all 848** pearl-countdown events over the rounds both
   games reached. A bot that recognises a known map can work out every tile's spawn attempts for all 500 rounds.
   **But tournament maps are unseen beforehand (confirmed by the organisers, 2026-09-23)**, so this only helps in
   ladder games, which set seeding.
4. **Every replay is public and contains its map.** `GET https://game.battlecode.au/api/matches/{id}/replay`
   (follow the redirect) returns gzipped Cap'n Proto with no auth. The `Replay.map` field is the full `.map` text,
   so the server's map pool can be collected from replays without an API key. Public replays have bot logs and
   per-turn CPU counts stripped.
5. **The top-ladder meta on 2026-09-23 was "split into a swarm, trade heads, wipe them out."** In 14 recent top games:
   up to the 64-dragon cap, ~100–1,200 splits per game (both teams), hundreds of deaths (mostly head-to-head), and
   7 of 14 games ended in a wipeout. The longest dragon at round 500 was only 4–42. See "Current meta" below. By
   2026-09-26 the leaders also grow one long king late and flood sonar (notes under "Current meta").
6. **Local matches are fast.** A starter-bot match takes about 0.5 s of engine time. The first build takes ~3.5 s
   native or ~12 s for the judge-sandbox wasm. After that the toolkit reuses the cached build, and a repeat match
   takes 0.6 s end to end.
7. **The server can be scripted.** A JSON API with Bearer key covers upload, activate, challenge, list battles and
   download replays. You can start 60 games an hour by hand. Unranked challenges on chosen maps don't touch Elo.

## Rules digest (see `docs/unsw/` for full text)

| Topic | Rule |
| --- | --- |
| Board | Rectangular grid, 10–64 per side, **wraps at the edges** (a torus). Symmetric: mirror in x, mirror in y, or 180° rotation (`SYMMETRY x/y/xy` in the map file). |
| Units | Up to 64 dragons per team. Each dragon runs its **own process** of the same program, with no shared memory. |
| Rounds | At most 500. Each round, pearls tick first, then every living dragon takes a turn in **ascending id order**. Moves apply one at a time, not simultaneously. |
| Win | A team with no dragons left loses (both wiped out in the same round = draw). After round 500: **longest living dragon** wins, then greatest total length, then draw. |
| Vision | 7×7 around the head (Chebyshev distance ≤ 3), wrapping. You can't see through portals. Each visible tile gives its coordinates, pearl, **pearl countdown**, dragon segments (team, id, head flag, direction) and all 4 edges. |
| Pearls | Each tile has a countdown. At 0 it spawns a pearl if the tile is empty (no pearl, no dragon), then redraws a countdown from its hidden range `[min_gap, max_gap]` either way. Mirror tiles share one countdown. A tile with `max_gap = 0` never spawns (countdown shows as −1). From 2026-09-25 17:00 the draws use a random seed per match, written in the replay (`unswbc run --seed` replays one). |
| Move | One step N/E/S/W. Eating a pearl grows the dragon by 1. Moving backwards puts your head into your own neck, which usually kills you. |
| Sprint | `MOVE NNE` takes several steps in one turn. Each extra step removes a tail segment, so x steps cost x−1 segments. Collisions and eating are checked at every step, and each pearl eaten mid-sprint pays for one more step. A step you can't pay for kills the dragon; the move is not cut short. |
| Collisions | Moving into kelp, any body segment (including your own tail, even if it's about to move) or another head (**both die**, any team). |
| Split | `SPLIT n`: the rear n segments become a new dragon, reversed, so the old tail is the new head. Needs child ≥ 2, parent ≥ 2 after, team < 64 alive. An illegal split kills the parent. The child gets a new id and a new process, and acts later **this** round. |
| Death | Every second segment, starting from the head, turns into a pearl: ⌈L/2⌉ pearls. |
| Sonar | Protocol 3 (unswbc 1.0.0, in our docs mirror from 2026-09-24 01:07 AEST; before that, one uint32 along the facing): up to **four** uint64 values a turn, one per direction, cast after your action in N, E, S, W order. A ray pointing backwards leaves from the tail, travelling the way the tail points. Each ray wraps, goes through portals, and stops at the first kelp or dragon segment of **either team** (it can hit the sender). Max range W+H. The receiver reads it at the start of its next turn; the sender gets **echoes**, counts of what its rays stopped on (kelp, ally body, ally head, enemy body, enemy head). A message carries **no sender or team**, so enemies can intercept or fake messages. |
| Output | You must send at least one valid action per turn, or the dragon dies. `MOVE`/`SPLIT` and `INDICATOR`: the last one sent wins; `SONAR`: the last one per direction. |
| Compute | 100M points per dragon per turn, 48 MB memory, single thread. Each stdout write costs 2.5M points plus 4k per byte; the C/C++ helpers buffer the turn and flush once, so log lines add bytes, not writes. A turn over budget returns nothing and the dragon dies. The first turn is charged for your setup work. Everything is deterministic: RNG is seeded per dragon per game, and the clock is the points counter. |
| Languages | Python (CPython 3.13 + NumPy 2.5.3), C17, C++20 (Clang 20, `-O2 -msimd128`, libc++). No third-party libraries unless you vendor the source. Zip ≤ 4 MB. |

## Engine facts read from source (not in the docs, or sharper than the docs)

- **Fixed pearl RNG (until 2026-09-25; now seeded per match, see fact 3).** `std::mt19937 mRng{PEARL_RNG_SEED}` with `PEARL_RNG_SEED = 0x5eed5eed`
  (`engine/include/engine/{types,game}.h`). At game start, every countdown-owning tile draws once in row-major
  order. Each round, any owning tile that reaches 0 draws a new gap whether or not the spawn was blocked
  (`engine/src/pearls.cc`). So the draw sequence depends only on the map.
  **Implication:** with the map's `TILE min max` lines, a bot can replay the RNG and know every spawn attempt on
  every tile for the whole game, including tiles it can't see. Whether a pearl actually appears still depends on
  whether the tile is occupied, and eaten pearls are only seen within vision.
- **Countdown ownership.** A tile and its mirror share one countdown, owned by whichever comes first in row-major
  order. On the fixed-point line of the symmetry, a tile is its own mirror.
- **Head-to-head order.** When A moves into B's head, B's death runs first and drops B's pearls, then A's
  (`engine/src/actions.cc`).
- **Sprint payment.** Each paid step pops one extra tail segment after the normal tail advance. A dragon of
  length L can take at most L−1 steps, plus one more for every pearl eaten on the way (`engine/docs.md`).
- **Split direction.** The child faces from its second segment towards its head, so it faces away from the parent.
- **Sonar hits.** `AliveDragonOccupying` on each tile the ray walks over. The first living segment hit gets the
  value, and that can be the sender after wrapping around. (Read from the protocol-2 engine; protocol 3 casts up to
  four rays this way.)
- **Result check** runs after every round. Game over on elimination, or at round 500 on
  (longest, total) (`engine/src/scoring.cc`).

## What the maps look like (the 9 in toolkit 0.3.13, plus 2 seen only on the server; as of 2026-09-23)

| Map | Size | Symmetry | Spawning tiles | Fast tiles (max gap < 100) | ~Spawn attempts / game | Kelp edges | Portal edges | Start dragons (both teams) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| arena | 11×11 | xy | 81 | 81 | ~2.9k | 22 | 0 | 2 |
| Colosseum | 16×16 | xy | 256 | 0 | ~1.6k | 24 | 8 | 2 |
| default_small | 16×16 | xy | 256 | 28 | ~0.8k | 72 | 0 | 4 |
| trophy | 25×25 | y | 625 | 11 | ~1.2k | 88 | 2 | 4 |
| queen_of_spades | 25×35 | xy | 442 | 32 | ~1.0k | 296 | 4 | 4 |
| default | 32×32 | xy | 1024 | 0 | ~0.8k | 216 | 24 | 8 |
| devil | 32×16 | y | 174 | 30 | ~9.8k | 244 | 0 | 6 |
| schooltime | 60×40 | y | 326 | 202 | ~3.8k | 913 | 24 | 6 |
| big_empty | 64×64 | xy | 4096 | 0 | ~5.5k | 0 | 0 | 6 |
| Trauma (server only) | 48×24 | xy | 142 | 12 | ~6.6k | 564 | 12 | 4 |
| stronghold (server only) | 48×24 | xy | 210 | 12 | ~7.0k | 302 | 8 | 4 |

**Turn order between teams.** In every map (bundled and server), starting dragons alternate A, B, A, B in the
file, so they get ids 0 (A), 1 (B), 2 (A)… and **team A's first dragon always moves first**. In a ranked battle
a team keeps its colour for all 5 games. In the 59-game team sample, side B won 36 and side A 23 (A 39% ± 12%).
That's suggestive but confounded by who challenges whom. The referee plays every pairing from both sides
anyway, and a same-bot self-play run will show whether moving second really helps.

The attempt counts are rough (500 ÷ mean gap, summed over tiles). The spread is huge: some maps are starved,
some have dense **pearl farms** (small clusters of fast tiles). The ladder has 9 active maps (`GET /api/v1/maps`,
2026-09-23): Schooltime, Big Empty, Queen Of Spades, Default, Default Small, Trophy, Devil, Stronghold and Trauma.
Arena and Colosseum are not in play (kept in `maps/extra/`). Stronghold and Trauma (both 48×24) weren't in toolkit
0.3.13; all 9 are in `maps/ladder/`. Every public replay also carries its map's full text. The Sprint's maps share
the idea of Dilemma (32×16) and Autarky (54×18), in `maps/sprint/`: each team starts with one very long dragon
(length 11 or 14) near the other's, in or beside rich pearl fields (organisers, 2026-09-25).

## Current meta (14 top-ladder games, downloaded 2026-09-23)

The games are the ten linked from `/top-battles` plus four more from the Sponge vs "dev test 1" series. Team names
are stripped from public replays, so "W" is the winning side and "L" the losing side. Numbers are dragons alive at
the end / longest / total length / splits / peak alive.

| Match | Map | End | Winner (W) | Loser (L) | Main cause of death |
| --- | --- | --- | --- | --- | --- |
| 73583 | Queen Of Spades 25×35 | wipeout r438 | 21/17/85/171/27 | 0/–/–/51/12 | head-to-head |
| 73768 | stronghold 48×24 | r500 | 33/9/109/366/58 | 27/4/73/177/51 | wall (L), self |
| 73954 | Devil 32×16 | wipeout r241 | 61/6/164/351/64 | 0/–/–/46/15 | head-to-head |
| 73973 | Trophy 25×25 | wipeout r358 | 38/6/116/204/41 | 0/–/–/118/19 | head-to-head (103 each) |
| 74074 | Trophy 25×25 | r500 | 17/**42**/133/164/28 | 12/8/44/157/30 | head-to-head |
| 74084 | Schooltime 60×40 | r500 | 64/22/475/352/64 | 3/20/38/225/38 | wall, head-to-head |
| 74105 | Trophy 25×25 | wipeout r115 | 39/3/92/81/40 | 0/–/–/18/12 | head-to-head |
| 74126 | Schooltime 60×40 | r500 | 50/19/343/402/64 | 42/13/196/822/64 | head-to-head |
| 74250 | Queen Of Spades 25×35 | r500 | 21/10/98/173/36 | 1/4/4/96/10 | wall, head-to-head |
| 74269 | Queen Of Spades 25×35 | wipeout r195 | 36/4/87/92/36 | 0/–/–/9/4 | head-to-head |
| 74270 | Trauma 48×24 | r500 | 10/12/58/57/16 | 22/10/91/42/28 | wall, self |
| 74271 | Big Empty 64×64 | r500 | 46/**39**/524/442/64 | 60/27/669/428/64 | head-to-head (358 each) |
| 74272 | Devil 32×16 | wipeout r93 | 37/4/88/76/37 | 0/–/–/18/10 | head-to-head |
| 74273 | Trophy 25×25 | wipeout r447 | 50/18/220/223/54 | 0/–/–/149/23 | head-to-head |

### Team profiles (59 games: last ≤3 ranked battles of each top-8 team, pulled 2026-09-23)

Each team is labelled from the battle metadata (a team keeps its colour for all games in a battle). All numbers
are per game. "Child/parent len" is the median length of the new child and of the parent after a split. "H2H
started" counts head-to-head collisions this team's mover caused. Death columns: wall/self/other body/head-to-head/
no valid action.

| Team (ladder rank) | Games | Win% | Wipeout% | Splits | Child/parent len | 1st split (rnd) | Peak alive | Pearls eaten | H2H started | Deaths (wall/self/body/h2h/nva) | Sprint % of moves | Sonar/turn | Longest at r500 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Sponge (1) | 15 | 80 | 53 | 145 | 2/2 | 2 | 35 | 588 | 43 | 121 (17/21/23/61/0) | 0.3 | 0.0 | 23.0 |
| dev test 1 (2) | 10 | 60 | 70 | 193 | 2/2 | 3 | 37 | 752 | 64 | 171 (6/19/24/122/0) | 0.3 | 0.0 | 22.7 |
| Settlers of Battlecode (3) | 5 | 60 | 40 | 177 | 2/2 | 0 | 35 | 481 | 22 | 162 (41/60/30/30/0) | 0.4 | 0.2 | 16.3 |
| PPP (4) | 5 | 40 | 40 | 435 | 2/2 | 0 | 44 | 1114 | 61 | 419 (169/88/66/96/0) | 0.0 | 0.0 | 9.7 |
| SSS (5) | 5 | 40 | 40 | 163 | 2/2 | 0 | 39 | 545 | 34 | 157 (0/31/33/80/13) | 0.5 | 0.0 | 17.3 |
| ddabap (6) | 15 | 60 | 40 | 263 | 2/2 | 0 | 41 | 986 | 83 | 235 (55/42/28/111/0) | 0.6 | 0.0 | 14.3 |
| Stockfish (8) | 9 | 44 | 44 | 265 | 2/2 | 0 | 43 | 829 | 31 | 234 (65/58/42/69/0) | 0.3 | 1.0 | 12.2 |
| Team SKKU | 20 | 40 | 40 | 159 | 2/2 | 0 | 35 | 541 | 23 | 140 (46/5/11/78/0) | 0.5 | 0.1 | 9.5 |
| okbro | 15 | 40 | 67 | 173 | 2/2 | 2 | 36 | 662 | 46 | 148 (24/4/20/99/0) | 0.6 | 0.0 | 16.4 |
| AGI ONE | 5 | 20 | 40 | 202 | 2/2 | 0 | 36 | 699 | 9 | 177 (0/0/0/84/**93**) | 0.0 | 0.9 | 18.3 |
| SHINK AI 6500 | 4 | 75 | 50 | 185 | 2/2 | 0 | 47 | 587 | 66 | 154 (33/4/16/101/0) | 0.2 | 0.0 | 14.0 |

Server maps seen in these games: Trophy, Trauma (48×24), stronghold (48×24), Queen Of Spades, Default, Big Empty,
Default Small, Devil, Schooltime.

What the profiles say (4–20 games per team, so treat this as a direction, not proof):

- **Everyone plays the same opening.** Split a length-2 child off every length-4 dragon from round 0–3, so the
  swarm grows like dividing cells, one division per two pearls eaten. Nobody sprints (≤0.6% of moves) and almost
  nobody uses sonar. (By 2026-09-24 the top teams flood sonar: see `leader-sonar/`.)
- **What separates the top team is not dying stupidly and winning trades.** Sponge (80% wins) has the fewest total
  deaths among the top 3, and good head-to-head numbers. PPP eats the most pearls (1114/game) but loses 169
  dragons a game to walls and wins 40%. AGI ONE loses 93 dragons a game to invalid/timed-out turns and wins 20%.
- **Round 500 is decided by a longest dragon of only ~10–23.** Nobody is deliberately growing one big dragon, so
  that's an obvious gap to exploit. (No longer true by 2026-09-26: leaders grow a king late; in 41 of our round-500
  online losses their longest at round 400 was median 18 to our 6, per `~/.bc-loop/briefing.md`.)

What this suggests (hypotheses):

- **Numbers win fights.** In every wipeout the winner split more and had 2–9× more dragons alive at its peak. Head-to-head trades kill both dragons, so the side with more small dragons comes out ahead.
- **Length decides close games.** At round 500 the winner is the team with the longest dragon: 74074 (42 vs 8) and
  74271 (39 vs 27, even though the loser had more total length and more dragons). A swarm that also grows and
  protects one long dragon covers both ways of winning.
- **Top bots still die a lot to walls and their own bodies** (hundreds of wall/self deaths in some games). Basic
  movement safety is not solved even at the top. That's cheap strength to take.
- **Sprints are rare** (under 1% of moves) and **sonar is optional** (some top bots send one ping per turn, others
  none). Both are under-used levers.
- **Server maps include ones the toolkit doesn't ship** (stronghold 48×24, Trauma 48×24). Collect the pool from
  replays or `/maps`.

## Strategy implications (hypotheses to test, not conclusions)

0. **Match the meta first, then beat it.** The baseline everyone at the top runs is the split-at-4 swarm. Our first
   bot must do that at least as well. Then look for gains where the meta is weak: dying less (top bots lose dozens
   of dragons a game to walls and their own bodies), predicting pearls (below), winning trades (next), and growing
   one long dragon for round 500 (nobody does).
1. **Survive first.** Elimination ends the game at once, and one dead dragon gives the enemy ⌈L/2⌉ pearls. Safe
   movement is the floor: reachable-area / flood-fill checks, head-to-head avoidance, tail rules, sprint legality.
   **Pick trades by length.** A head-on collision kills both dragons, so trading a length-2 dragon for a length-6
   one is a win. Ramming a body kills only the rammer, so a body placed across an enemy head's only exits is a free
   kill.
2. **The winner is decided by the longest single dragon.** Many small dragons gather more pearls (more heads, more
   of the map covered), but length can't be merged. The only way to move length between dragons is for one to die
   so another eats its pearls, at 50% efficiency. Possible plan: small "feeders" gather, and a protected "king"
   grows. The split/feed/king balance, and when to consolidate before round 500, is the main thing to tune.
3. **Small dragons can kill big ones.** A long dragon dies if its head hits any body. Moves are sequential, so a
   small dragon that puts its body across a big dragon's only exits kills it: the Tron/Battlesnake cut-off. The
   defence is keeping escape room, and sprinting (a long dragon can pay for sprints).
4. **Turn order is information.** Higher-id dragons act after lower-id ones in the same round and see the updated
   board. A split child always gets a higher id than every existing dragon.
5. **Map knowledge.** (The pearl-schedule part is gone since 2026-09-25: schedules are seeded per match.)
   Embedding all known maps (kelp, portals, pearl beds, the RNG-replayed pearl schedule) turns a
   7×7 view into near-full knowledge of the static map and pearl timing. The bot identifies the map from its size,
   start position and visible features, and falls back to general play on unseen maps. **Tournament maps are
   unseen beforehand (confirmed by the organisers)**, so this only pays on the ladder. The bot must be strong
   without it.
6. **Sonar is a narrow, forgeable channel.** Useful for alignment ("I'm the king", target claims), but each ray
   only reaches the first dragon in a straight line (four rays a turn since protocol 3). Authenticate our own
   messages (keyed checksum bits; the bot has used a per-build SipHash tag since v0023). Hypothesis: some opponents
   trust any sonar message, so fake messages could exploit them. sig decided fake messages are allowed
   (2026-09-23); rivals say they replay and forge ours (Discord claim, 2026-09-25).
7. **Compute.** In C++, 100M points allows hundreds of BFS passes per turn, or a real local search (sprint
   sequences, small minimax over nearby heads). One stdout write per turn (2.5M points). The first turn must not
   precompute everything at once.
8. **Elo scoring rewards blowouts.** Score is games won out of 5. Against a team rated more than ~70 below you, a
   3–2 win loses rating (the expected share there is above 0.6). Consistency across maps matters more than a high
   ceiling on a few.

## Competition logistics

**Timeline (from `/tournaments`, 2026-09-23):**

| Event | Date | Format | Prize |
| --- | --- | --- | --- |
| Sprint | 1 Oct, online | Seeded knockout, all teams (647 listed), best of 5 | $800 / $400 |
| Qualifiers | 10 Oct, online | Seeded knockout, eligible teams only (175), best of 5, top 10 advance | – |
| Grand Final | 17 Oct, UNSW Sydney | 10 qualifier winners, best of 5 | $8,000 first; $18,500 across top six |

Seeds are "tentative" and use the current ladder rating, with ties broken by total wins. A tied knockout match
sends the higher seed through; the Sprint snapshots bots and ratings the day before 1 Oct, and Qualifier
submissions close 9 Oct (lmkae, Discord, 2026-09-21/23).

**Rules that matter** ([`docs/unsw/site/terms.md`](../docs/unsw/site/terms.md),
[`about.md`](../docs/unsw/site/about.md)):

- The Grand Final seeds the 10 finalists **by ladder rating on 16 Oct**. Seeds 1–6 skip the play-in round, so
  ladder Elo still counts after qualifying.
- Fair play: "Attempting to circumvent the sandbox, access the network, interfere with other teams, or exploit the
  platform will lead to disqualification." Submissions "must be your team's own work". The terms don't mention
  AI-written code, embedded map data or in-game sonar tricks. The organisers have since allowed AI use (reported by
  sig, 2026-09-23); sig decided embedded maps and fake sonar are allowed (CLAUDE.md, Decisions). The fixed pearl
  seed was removed on 2026-09-25.
- The Grand Final and its prizes need every team member to be enrolled at an APAC university/high school (or be an
  APAC citizen studying elsewhere). The ladder and the Sprint are open to everyone. Teams are 1–4 people, one
  account per person.
- Extra awards: **Jump Trading Grand Award for the "fastest algorithm" among finalists** (so keeping CPU use low
  is worth something), HRT bug bounty for the worst platform vulnerability reported (report bugs, never exploit
  them), plus Top UNSW Team, First Year and WGM prizes ($500 each).
- Finalists' travel (capped at ~A$400) and accommodation in Sydney are covered. Anyone who can't travel because
  of visas competes remotely.

**Ladder mechanics** (`docs/unsw/game-format.md`, `elo.md`): one Elo rating per team, starting at 1500, updated once
per five-game ranked battle. K is per submission: 96 for a new bot, falling to 24 over its first 10 ranked battles,
and only one new bot per team in 12 hours starts from 0 (docs as of 2026-09-25). **Autoscrims** run every 2 hours on the UTC boundary: up to 2 opponents from the 8 places
above and 8 below, ranked, and they don't use your hourly allowance. By hand: 60 games/hour (12 ranked battles). A
ranked challenge can only target teams rated no more than 50 below you. Uploads: 12 per hour, 4 MB each. A new
upload goes active as soon as it builds, and the active version is what plays autoscrims.

**API** (`docs/unsw/api.md`): base `https://game.battlecode.au/api/v1`, `Authorization: Bearer bc_…`,
120 requests/min (30/min for `/leaderboard` and `/ratings`). Covers submissions (upload / activate / download /
build log), battles (challenge / list / get / replay), leaderboard, ratings, teams, tournaments, maps and queue.
Unauthenticated: `https://game.battlecode.au/api/matches/{id}/replay` (the visualiser uses it). Top matches are
listed at `/top-battles`.

**Ladder snapshot, 2026-09-23 ~16:50 AEST** (329 teams with bots):

| Rank | Team | Elo | Lang |
| --- | --- | --- | --- |
| 1 | Sponge(Albert and Bob) | 1994 | cpp |
| 2 | dev test 1 :P | 1994 | cpp |
| 3 | Settlers of Battlecode | 1941 | cpp |
| 4 | PPP | 1918 | python |
| 5 | SSS | 1910 | cpp |
| 6 | ddabap | 1909 | cpp |
| 7 | Vibing++ | 1905 | cpp |
| 8 | Stockfish | 1891 | cpp |
| 9 | 龙虎豹 | 1890 | cpp |
| 10 | Check Raise | 1873 | cpp |

Elo at rank 20 ≈ 1831, rank 50 ≈ 1683, rank 100 ≈ 1554. Languages: 223 cpp, 100 python, 6 c.

## Local tooling (measured 2026-09-23 on this machine, load ~7.7 on 10 cores)

| Run | Wall time | Notes |
| --- | --- | --- |
| `unswbc run default.map pybot pybot` (native) | 0.7 s | engine time 0.2 s, 162 rounds |
| `unswbc run default.map cppbot cppbot` (native) | 4.1 s | ~3.5 s is the native build; match 0.5 s |
| same with `--sandbox` | 13.2 s | judge clang → wasm build dominates; starter C++ uses ~3.7M points/turn |
| `pybot` with `--sandbox` | 9.7 s | starter Python uses ~5M points/turn (the helper's parsing) |
| `big_empty.map dummy murder` (native) | 13.7 s | engine time 10.3 s, 500 rounds, both sides splitting |
| `schooltime.map suicidal dummy` (native) | 2.0 s | engine time 1.5 s, wipeout at round 199 |
| same C++ match again (build cached) | 0.6 s | the toolkit fingerprints sources (sha256) in `.unswbc-build/` and skips unchanged builds |

Takeaways: the toolkit already builds once and caches, so calling `unswbc run` repeatedly is fine. Pass
`--no-replay` when the replay isn't needed. `--sandbox` also applies the judge's caps on logs, indicators and
drawings. (The docs mentioned a `--judge-limits` flag until the organisers fixed them on 2026-09-23; the tool never
had it.) The engine is `unswbc_engine.wasm` under
`wasmtime`, and bots are separate processes (a "zygote" process forks Python bots). A heavy bot on a full map is the costly case: up to 64
dragons × 500 rounds per team, each doing real search. That needs measuring, and an evaluation budget designed
around it (see the plan). The toolkit ships sparring bots in `examples/`: `dummy`, `suicidal`, `murder` (C++),
`stress`, `fuzz`, `dumbass`, `test`, `cppbot`.

## Open questions to settle first

1. ~~Does the judge use the same pearl seed?~~ **Yes until 2026-09-25**, checked on Trophy: 848/848 countdown events
   identical. **From 17:00 that day, no:** each match gets a random seed, recorded in its replay.
2. What is the full server map pool? Harvest it from public replays (each carries its map text) or `GET /maps`
   once we have a key. **Settled for tournaments: they use maps that aren't released beforehand (confirmed by
   the organisers to sig, 2026-09-23).**
3. ~~Is the user's team eligible for the Qualifiers and Final?~~ **Yes** for the Sprint and the Grand Final
   (CLAUDE.md, Decisions). Since the 2026-10-03 merge (UNSW, Melbourne and Monash members) not for the Top UNSW
   Team prize.
4. Do the organisers consider embedding map data, predicting pearls from the fixed seed, or faking sonar to be
   fine? (Moot for pearls since 2026-09-25: they removed the fixed seed rather than rule on it.) The terms ban
   exploiting the *platform* and "interfering with other teams". **Decided by sig, 2026-09-23:** known-map tricks
   (ladder-only) and fake sonar are allowed; no organiser ruling is recorded.
5. How fast is local simulation with a heavy C++ bot, and would a native (non-wasm) engine build be faster? The
   engine is ~2k lines of C++ with CMake (`engine/`). **Partly measured (E0085, 2026-09-26):** a native test game
   spends ~5.9 s CPU in the engine against ~2.8 s for both bots; loading the wasm engine precompiled
   (`referee/engine_cache.py`) saves ~18%. **Measured 2026-09-27:** starting ~800 bot processes a game (one per
   dragon) cost ~35% of the rest; forking them from one pre-started copy of the bot (`referee/fork_server.py`)
   saves ~31% of a native game's CPU (60 map pairs, byte-identical replays).
6. Does the per-turn budget depend on language? `/about` and the terms say runtime limits "scale based on the
   language used". The Timeouts page gives one figure (100M points per dragon per turn) and prices Python by the
   instructions its interpreter executes. Trust the Timeouts page until the organisers say otherwise.
