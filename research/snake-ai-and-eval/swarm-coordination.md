# Swarm coordination without shared memory, and micro when head-on kills both

Setting (rules verified this session in `docs/unsw/`): up to 64 dragons per team, each its own process of the same program, no shared memory; 7×7 vision around the head; the only channel is **sonar**: up to four uint64 values per turn, one per direction, each delivered to the **first dragon segment of either team** its ray hits (possibly the sender), with no sender or team attached; the sender gets echo counts of what its rays hit (`sonar.md`, protocol 3 from 2026-09-24). Turns are **sequential in ascending id order**; starting ids follow map-file order; a split child gets the next unused id and acts later the same round (`structure.md`, `execution-order.md`, `map-files.md`, `splitting.md`). Head-on = **moving your head into another dragon's head**; both die (`death.md`). Every run is deterministic given its seed: "RNG is seeded per dragon per game, and the clock is the points counter" (rules digest in `research/unsw-game-analysis.md`); since 2026-09-25 each match draws a random pearl seed.
Legend: **[V]** verified from a cited source this session; **[I]** my inference; **UNVERIFIED**.

---

## 1. Coordination by shared code instead of shared memory

**Core idea [I]:** every dragon runs the same deterministic program. If two dragons see the same inputs, they compute the same answer. So a dragon can *simulate its teammates' decisions* instead of talking to them — as long as the inputs are common knowledge to both.

What breaks this, and the fix:
| Divergence source | Fix |
|---|---|
| Different vision (7×7 each) | Only coordinate on facts both can see (e.g. two dragons whose heads are within 3 of each other see each other and most of the same cells). Beyond that, fall back to rules that need no agreement (§2). |
| Per-dragon RNG seeds | Never use the RNG for a decision that must agree. If randomness is needed, derive it from shared facts: hash(round, map id, cell coords). |
| Time-budget-dependent search (anytime search stops at different depths) | Agreement-critical decisions must use a fixed-work computation (fixed depth / fixed node count), not "search until the budget runs out". The points counter is deterministic, but different dragons have different amounts of other work that turn. |
| Order dependence | Canonical orderings everywhere: iterate candidates sorted by (score, then cell index, then dragon id). Break every tie by a fixed key, never by container iteration order or pointer values. |
| Floating point | Same binary + same inputs → same floats, so this is safe within our own team; still prefer integer scores for tie-heavy comparisons. |

**Evidence this pattern is standard:**
- MIT Battlecode: "As each robot runs its own independent copy of the code, the Battlecode game typically features some method of inter-robot communication" — Isaac Liao (7th, 2021) https://battlecode.org/assets/files/postmortem-2021-wololo.pdf [V]. Top teams assign each unit a **role** and broadcast it so others know its intentions (same source §4) [V].
- CBBA (consensus-based bundle algorithm), the standard decentralised multi-robot task auction: agents greedily bid on tasks, then resolve conflicts by "highest bid wins", **ties broken by lower agent id** (search summary of Choi, Brunet, How 2009 "Consensus-Based Decentralized Auctions for Robust Task Allocation" https://www.researchgate.net/publication/224504322 ; tie-break detail from search-result summary, paper not read — UNVERIFIED verbatim). CBBA needs message rounds we don't have, but its **conflict rule** is exactly the deterministic tie-break we can apply locally.

## 2. Task allocation with (almost) no messages

Ranked by how little agreement they need [I unless cited]:

1. **"Only chase food you reach first" (Voronoi claim).** Graeme Hill's winning 2018 Battlesnake "defensive hungry" policy: go for a pearl only if you can reach it before any enemy (http://web.archive.org/web/20240224101502id_/http://graemehill.ca/battle-snake/) [V]. Extend to teammates: a pearl belongs to the friendly dragon with the smallest (distance, id). Each dragon computes this over the dragons *it can see*; conflicts only arise at the edge of vision and resolve themselves next turn when the loser sees the winner. Zero messages.
2. **Territory by id / by map region.** With known maps (ladder only: tournament maps are unseen, and pearl schedules are seeded per match since 2026-09-25), pre-assign regions or pearl beds to roles derived from id at split time (e.g. child of dragon k takes the bed nearest its birth cell). Needs no comms; brittle only if dragons die.
3. **Prioritised planning (right of way by id).** In multi-agent pathfinding, agents plan in a fixed priority order; each plans around the reserved paths of higher-priority agents (Silver 2005, "Cooperative Pathfinding", AIIDE; Erdmann & Lozano-Pérez 1987) — standard technique, not re-read this session (UNVERIFIED citation details). In our game this is **free**: lower ids move first and higher ids see the result on their turn (`structure.md`) [V]. Rule: lower id ignores higher-id teammates' possible moves only where they can't collide; higher id yields.
4. **Sonar for the few things that need agreement** (§3): "I am the king", "pearl X is claimed", "enemy head at (x,y)".
5. **Distributed gradient fields over sonar** (like Liao's distributed Bellman-Ford over flags: each unit shows its distance to a feature and takes min over neighbours' value + distance) [V source above]. Each sonar ray only reaches the first dragon in its line (four rays a turn since protocol 3), so this is weaker here; probably not worth it [I].

**Stigmergy [I]:** bodies are messages. A friendly body across a corridor says "this area is mine / sealed". A dragon can read teammates' facing and body shape in its 7×7 view and infer intent (e.g. a teammate facing a pearl within 2 is going for it). Cheaper and unforgeable compared to sonar.

## 3. Sonar protocol design (spoofable channel)

Facts [V `sonar.md`, protocol 3 from 2026-09-24]: up to four uint64 per turn, one per direction, cast N, E, S, W *after* the move (a ray opposite the facing leaves from the tail, travelling the way the tail points); the sender gets echo counts next turn; wraps; passes portals; stops at the first kelp or dragon segment of either team; may hit the sender; lost after W+H tiles; delivered at the start of the receiver's next turn ("a higher id receives it this round and a lower id next round", `execution-order.md`).

**Real precedent for spoofing:** MIT Battlecode 2020 messages were public and anonymous. In the final tournament, team Prasici **re-broadcast Steam Locomotive's own first message**, "causing Steam Locomotive's landscapers to run towards the edge of the map, trying to get to where they thought their HQ was." The High Ground (1st seed) was safe because they "change[d] our communication encryption constants right before we submitted" — https://battlecode.org/assets/files/postmortem-2020-the-high-ground.pdf [V]. That was a **replay attack**, which a plain secret checksum does not stop.

Design [I]:
- Split the 64 bits into payload + authentication tag, e.g. 32-bit payload + 32-bit tag → a random forgery passes with probability about 1 in 4 billion per message (the 2026-09-23 rules had 32 bits, so 20 + 12 and 1/4096). As built, the bot uses a 51-bit body and a 13-bit tag (`wire::BODY_BITS` in `bot/belief.hpp` on the `loop/champion` branch, 2026-09-26).
- Tag = keyed hash(secret key, payload, **round number**, **receiver-checkable context** such as the sender's head cell or the receiver's head cell). Binding to round defeats replay of old messages; binding to position defeats re-casting from elsewhere. The receiver knows the round and can check the claimed geometry (the ray came along a straight line from ahead of it).
- Rotate the key per submission (as The High Ground did), since all replays are public and a clever opponent could try to learn the scheme.
- Encode positions compactly: Liao sent coordinates **mod 128** because the map was ≤64 wide, so any receiver can recover the unique location [V]; on our ≤64 torus 6 bits per axis are exact. Use log scale for large quantities (Liao) [V].
- Treat every unauthenticated message as enemy input. Never let a sonar value trigger a dangerous action by itself.
- Faking messages to exploit opponents' unauthenticated sonar: sig decided it is allowed (2026-09-23; CLAUDE.md, Decisions).

## 4. How sequential turn order changes search [I, from the verified rules]

Battlesnake, Hungry Geese and CodinGame snake games are **simultaneous**: bots must guess opponents' moves, and search either assumes "opponent replies knowing my move" (paranoid, too pessimistic — m-schier README, battlesnake.md §5) or uses decoupled bandits (DUCT/Smitsimax, see hungry-geese-and-tron.md, competitive-bot-culture.md). **Our game is a true alternating-move game in id order.** Consequences:

1. **Dragons with lower id than me have already moved this round.** Their positions are facts, not guesses. Dragons with higher id will move after me and **see my move** — an honest min node follows my max node. Plain minimax/alpha-beta is exactly right, not a pessimistic approximation.
2. **A search ply = one dragon's turn**, in id order, skipping dragons outside the local window (locality, battlesnake.md §5). Tree for me + 3 nearby dragons, depth 1 round = 4 plies; branching ≤3 each (no reversing).
3. **Head-on is an active choice by the mover, not an accident of simultaneity** (`death.md`: "Moved into another dragon's head"). So the dragon that moves *later* when two heads are adjacent decides whether to trade. Practical rules:
   - If my head ends my turn **adjacent to an enemy head that moves after me this round** (higher id), that enemy may trade on its turn. If it moves before me (lower id), it gets the option next round unless I leave. Moving adjacent to an enemy head = offering a trade.
   - I can take a trade when my head is adjacent to an enemy head on my turn. Take it iff the trade is good for us (§5).
   - Because turns are sequential, "both move into the same empty cell" cannot happen: the second mover would see the first's head there and choose to ram (trade) or not.
4. **Id order is a map-dependent, persistent asymmetry** (starting ids = file order; children get new high ids). A freshly split child always moves last in the round, i.e. after every existing enemy dragon [V `splitting.md`] — it always gets to see and react, but it is also the last to escape a trap.
5. **Quiescence**: never evaluate a leaf while an enemy head is within 2 cells of a friendly head (Shapeshifter extends up to 5 plies in that case, battlesnake.md §3 [V]).
6. **Teammates in the tree**: teammates are not adversaries. Model them with the shared deterministic policy (§1) at leaf cost, not as search nodes, unless they are within 1–2 cells.

## 5. Swarm micro when a head-on kills both

**Trade value [I].** When dragon of length a (ours) and b (theirs) both die, ⌈a/2⌉ + ⌈b/2⌉ pearls drop near the collision. Who benefits depends on who eats them. A simple value model:
- Material: we lose a, they lose b. Good for us if b > a, and also if dragon count matters (wipeout) and we have more dragons.
- Pearl recapture: the nearer team's dragons collect most of the ⌈(a+b)/2⌉-ish pearls. A trade next to our swarm is worth more.
- Tiebreak: the game is decided by **longest dragon**, then total length. Trading a length-2 dragon for their longest dragon can flip the result outright.
- Evidence from the current meta (brief + game analysis): top teams run 35–47 small dragons, **most deaths are head-on**, nobody sprints, and the longest dragon at round 500 is only ~10–23. So cheap length-2/3 dragons are plentiful, trades are the main form of combat, and a single protected long dragon wins the tiebreak.

**Micro rules worth testing [I, each is a hypothesis for the testing loop]:**
1. **Offer trades only down in value**: a short dragon may move adjacent to (or into) an enemy head that is longer; a long dragon keeps ≥2 cells from any enemy head unless it has an escape and moves after the enemy.
2. **Protect the king**: the designated longest dragon never ends a turn within reach of an enemy head that moves after it; escorts (short dragons) sit between it and enemy heads so any enemy approach meets a cheap trade first.
3. **Body-blocking kills**: moving into a body kills only the mover (`death.md`) [V]. A body placed across all exits of an enemy head is a free kill. Sequential turns make this computable: for each enemy head within 3, count its legal next cells after my move; if 0, it dies on its turn. Tron/Battlesnake cut-off logic applies (battlesnake.md, hungry-geese-and-tron.md).
4. **Multi-dragon traps**: two friendly short dragons can close both sides of an enemy's escape in one round if the lower-id one moves first and the higher-id one completes the trap (prioritised order is free).
5. **Don't collide with teammates**: friendly head-on also kills both. Sequential order prevents same-cell simultaneous moves, but a teammate moving later can still ram my head. A shared rule — "never move into a friendly head" — is enough because all our dragons run it.
6. **Endgame consolidation**: length can't be merged except by dying and being eaten at ~50% (⌈L/2⌉ pearls). Before round 500, feeders near the king can die deliberately (e.g. into walls/kelp) next to it so the king eats their pearls — hypothesis, check it beats simply keeping them alive for the total-length tiebreak.

## 6. Adjacent evidence from other games
- Battlesnake Snakes AI 2020: leading snake **circled the apple to deny it** and won on time; organisers changed rules in response (https://arxiv.org/pdf/2108.05136) [V] → once ahead on the tiebreak, denial + safety is a legitimate winning mode.
- CodinGame SnakeByte 2026 (up to 4 snakes per player): **per-snake search with local pairing of nearby own snakes beat joint search over all own snakes**; one player clustered snakes by union-find (Manhattan ≤12) and searched clusters independently for a big speedup (competitive-bot-culture.md §1.1) [V via that file]. Matches our forced per-dragon structure.
- Hungry Geese 2nd place: their best agent "too often placed 3rd due to head-on collisions with a shorter goose that the neural network predicted would never move towards us" — fix was modelling opponents as partly random (hungry-geese-and-tron.md) [V via that file]. Lesson for us: never assume an enemy head won't come at us; assume any legal move is possible for close enemy heads (sequential turns let us do this exactly with minimax).

## Out of scope / leads
- Read MIT Battlecode 2023/2025 postmortems (https://battlecode.org/assets/files/postmortem-2023-dont-at-me.pdf , https://battlecode.org/assets/files/postmortem-2025-om-nom.pdf) for more micro/comms patterns; downloaded but only grepped.
- Check in public replays whether any top team's sonar values look structured (would indicate they coordinate, and whether they authenticate).
- Resolved: fake sonar and known-map tricks (ladder-only) are allowed (sig's decisions, 2026-09-23).
