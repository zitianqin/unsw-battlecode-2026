# Recurring Battlecode techniques, detailed enough to implement

Scope: techniques that recur across MIT Battlecode postmortems, each followed by how it maps onto UNSW Battlecode 2026: torus snake game with N/E/S/W moves; up to 64 independent dragon processes; **7×7 vision around the head**; kelp (walls on tile edges) and portals; spoofable sonar (one uint32 along the facing on 2026-09-23; from 2026-09-24 up to four uint64 a turn, one per direction, with echo counts); sequential turns by id; 100M CPU points per dragon per turn in a WASM sandbox. The UNSW rules here come from ../unsw-game-analysis.md.

Evidence tags: **[XSq]** = Ivan Geffner, "A Guide to Battlecode", https://battlecode.org/assets/files/battlecode-guide-xsquare.pdf (2020 winner, veteran since 2015). Other tags point to sections in postmortems.md. "Inference" = my mapping to the UNSW game, not a claim from the source.

---

## 1. Pathfinding

### 1a. Bug navigation (the universal fallback)
- **What it is:** walk straight toward the target. When blocked, pick a side and follow the obstacle's wall with one hand until you are again on the straight line to the target (Bug2) or until you are closer than where you started following (Bug1/"Bug0 with memory"). The state you keep is the direction you were following the wall in, the side (left/right), and the distance at which you began following.
- **Who uses it:** XSquare used BugNav all of 2020 and won (JBW postmortem). The 2025 winner's lineage used camel_case's unrolled Bellman-Ford with a **fallback to bugnav after 3 turns without progress** (confused 2025). GST 2026 used a stack-based bug nav from Gone Fishin' 2023 and handed off to it when BFS stopped making progress.
- **Pitfalls with fixes:**
  - **Target-switch infinite loop [XSq §5.14]:** if a unit re-targets to "nearest target" while wall-following, it can loop forever. Fix: switch from target T to T′ only if d(R,T′) < *the minimum d(R,T) seen since T became the target*. XSquare says this guarantees that a target is eventually reached.
  - **Units as obstacles:** treat friendly units as passable when planning and as blocked only for the immediate step (Om Nom 2025 "Optimal Bug").
  - **Loop detection:** remember recent positions or turn points. If you return to one, flip the follow side (GST 2026; Battlegaode 2020 "remember last few positions → fall back to bug").
- **Inference for UNSW:** the torus has no map edges, but there are static kelp edges, portals, and moving snake bodies. Vision is only 7×7. On a map the bot knows (ladder maps can be embedded; tournament maps are unseen, organisers 2026-09-23), BFS over the full static map replaces bug nav. On an unseen tournament map, bug nav with loop detection is the right fallback until the kelp layout is learned. Either way, **keep a guaranteed-progress fallback plus loop detection** for when the planner fails.

### 1b. Local BFS / Bellman-Ford over the vision window, unrolled
- **Unrolled "BFS" DP [XSq §6.1]:** for 2021's passability tiles, t(L) = p(L) + min over L′∈S(L) of t(L′), where S(L) is the set of neighbours of L strictly closer to the origin. You compute it in ring order outward from the origin. Every loop index and neighbour set is fixed relative to the origin, so a **script generates fully unrolled code**. If the target is outside vision, move toward the vision-boundary tile L that maximises (d(O,T) − d(L,T)) / t(L).
- **Bellman-Ford with 1–3 sweeps** (Battlegaode 2020: a 5×5 grid seeded with distance-to-target × 100, 3 iterations; Baby Ducks 2021: 1–2 unrolled sweeps beat Dijkstra because they are easier to unroll).
- **Precomputed offset tables in BFS order** (Bowl of Chowder 2020): a generator emits all (dx,dy) in nearest-first order, so scanning vision needs no queue or visited set. JBW 2020 instead walks a hard-coded *direction sequence* that visits every visible cell, where `loc.add(dir)` costs 2–3 bytecode vs more than 15 for constructing a new location. That saved more than 2000 bytecode per turn.
- **Bitmask BFS** (Om Nom 2025 "Optimal Bug" at about 1.5k bytecode; GST 2026 reachability at about 300 bytecode by bit shifts): store passable tiles as bits in machine words. Each BFS layer is `next = (cur | cur<<1 | cur>>1 | up(cur) | down(cur)) & passable & ~visited`.
  - **Inference, highly relevant:** a 64×64 torus is exactly **64 uint64 rows**. Wrap-around is a bit *rotate* on the x axis and a row index mod 64 on y. Kelp is on tile *edges*, so keep one "can step east / can step north" mask per direction instead of a single passable mask. Portals need a small fix-up per layer. A full-map multi-source flood fill (for example from every known pearl or from every head in vision) costs about 64 word-ops per layer × ≤64 layers ≈ 4k word-ops per direction. That is orders of magnitude under the 100M-point budget in C++. Use it every turn for distance fields and "who reaches this tile first" (Voronoi) maps, restricted to the heads you actually know about.

### 1c. Distance fields / "who gets there first" maps
- Postmortems mostly use a *single-source* BFS toward one target because of bytecode. The recurring pattern is to compute a field once and let every unit read it. JBW 2020 had the HQ compute a wall plan by BFS and broadcast a bitmask so that all units derive the same classification. Smite 2019 had every unit run the *same* BFS over resource tiles to get an identical ordered cluster list.
- **Inference:** in UNSW each dragon has 100M points, so every dragon can afford full-map fields over *static and predictable* state: the map, kelp and portals of a known map, and the fixed-seed pearl schedule (gone from 2026-09-25, when seeds became per match). Every dragon computes the same answer without communicating (see §5). Dragon positions outside 7×7 vision are unknown, so fields over heads can only use what the dragon has seen, remembered, or been told by sonar.

---

## 2. Communication

### 2a. Bit-packing into fixed-width messages
- JWU 2025 (1st) packed a 32-bit message as **3 bits type, 12 bits x/y, 2 bits payload**, with spare bits reserved.
- SPAARK 2025 packed **two 16-bit records per 32-bit message**: 12 bits of location, 3 bits of type/team, and 1 relay bit. The all-ones type was reserved for a symmetry message.
- California Roll 2021: map-relative coordinates in 14 bits (x mod 128 on a ≤64 map with random offsets). Smite 2019: an 8-bit zone index (256 zones), or a 5-bit index into a deterministically computed cluster list, instead of a 12-bit x,y.
- GST 2026: a **3-bit type header**, with each message type a class behind a sealed interface. This makes types easy to add and debug.
- **Inference for the UNSW sonar (uint32, forward ray, first segment hit, as on 2026-09-23; protocol 3 gives 64 bits per ray, so there is room for a much longer tag):** a 64×64 position needs 12 bits, so 20 bits remain. A workable layout is 4 bits type, 12 bits position, and 8 bits payload such as a length or round mod 256. That leaves 8 bits for an authenticity tag (see 2c).

### 2b. Relay / "squeak everything you see" when vision is local
- Baby Ducks 2021: sightings of enemy muckrakers were relayed robot to robot, so slanderers fled threats beyond their own vision.
- GST 2026 "squeak maximisation": in combat every rat broadcast its HP, the nearest enemy's HP, its direction and its location **every turn**. They went over bytecode in crowded fights and judged it "a very worthy tradeoff".
- **Inference:** the UNSW sonar is directional, so each ray only reaches the first segment in its line. That segment may be an enemy. On 2026-09-23 relaying was possible only along chains of our own dragons facing each other; since protocol 3 (2026-09-24) a dragon can ping in all four directions, so relays need only a line. It is useful mainly for tight formations such as parent–child pairs right after a split.

### 2c. Anti-spoofing (and spoofing)
- **Real attacks happened.** In the 2020 finals, Prasici replayed Java Best Waifu's recorded messages to mislead them about their own HQ location. It failed only because the replay was at the wrong round. XSquare said a pattern-deducing attacker "would have succeeded" (JBW 2020; Bowl of Chowder 2020 also records it).
- **Defences used by winners:** smite 2019 wrapped every read and write in encrypt/decrypt **keyed on per-map features** (fuel layout, map size), so signals differed every game and recorded ones could not be replayed. The High Ground 2020 changed its "encryption constants right before we submitted".
- **Inference for UNSW:** all replays are public and the sonar carries no sender, so any fixed encoding will be read and forged by rivals watching replays. Cheap defence: tag = hash(secret, round, receiver-position or map-seed) truncated to 8 bits, so a forged message is accepted with probability 1/256 (with 64-bit sonar the tag can be 32 bits or more). A rival can still *jam* by pinging random values, so never let a single unauthenticated message trigger a fatal action. Offence: forging rivals' plaintext pings is possible if they use them, but the current meta shows sonar is nearly unused, so this is low priority.

### 2d. Communicate less; derive more
- Battlegaode 2020: "units would mostly make their own decisions … based on the positions of nearby units".
- XSquare [§5.9]: "if some global functionality can be approximated by independent behavior from each robot, this is usually the way to go."
- Om Nom 2025 inferred hidden global state from cheap observables (their number of paint towers from the change in team money).
- **Inference:** UNSW dragons see only 7×7 but can *know* a lot without seeing it: the map (ladder maps embedded; tournament maps are unseen), the pearl spawn schedule (fixed-seed RNG, replayable; no longer since 2026-09-25, when the organisers seeded it per match), their own starting layout, and their own id order. Coordinate through that shared deterministic knowledge first, then through local sensing, and use sonar last. See §5.

---

## 3. Micro (tactical per-unit decisions)

### 3a. XSquare-style candidate-square scoring (the dominant micro template 2021–2026)
- For each candidate action (8 moves plus stay; in 2026 also every move × turn × throw combo), compute a small struct: can I move there, minimum distance to an enemy, damage I can deal, damage I can take, whether I am inside a tower's range, and so on. Pick the best with an **ordered tie-break chain** (if/else on the most important feature first).
  - Om Nom 2025 used this. GST 2026 enumerated all valid move+turn+throw combos and designed the factors in a spreadsheet, and some combos had to be banned empirically.
- **Single-currency scoring** (SPAARK 2025, HS 1st): convert everything to one unit (5 score = 1 paint). Examples of their terms: +20 for the correct direction, +15 for an adjacent direction, −5 per paint for a tile penalty (doubled if you can't move next turn), −5 per adjacent ally, a large penalty inside enemy tower range, and **−1 for tiles visited in the last 5 turns (anti-oscillation)**. "Taking all the game constants at face value is enough to be competitive with top teams." **Evaluate both move-then-act and act-then-move orders.** They missed this in 2023 and 2024.
- **Value thresholds:** fire or act only if expected value > cost. Confused 2025 fired a splasher only if the 3×3 value sum was > 50, the attack's cost. Baby Ducks 2021 empowered only if `100*(kills−2) + (drained − spent) > 0`.
- **Inference for UNSW:** each dragon's turn is a choice among ≤3 headings × (sprint 1..k) × (split n or not). That is tiny, so enumerate it exhaustively and score it. Key features:
  - Is the destination tile occupied **now**, given the sequential turn order? Dragons with lower ids have already moved this round, and higher ids have not.
  - Could an enemy head reach this tile this round, which means a head-on or body-kill risk?
  - Resulting length.
  - Pearl gained.
  - Distance field advantage.
  - Where the ⌈L/2⌉ pearls drop if a trade happens.

### 3b. Threat geometry and turn-order exploitation
- GST 2026: the 2–3 tiles directly in front of a cat were the most lethal zone. Avoiding them mattered, but must not override enemy-avoidance.
- JWU 2025 (1st): kite (attack, then step out of range). In the finals they **synced soldiers to enter tower range only on even turns** so the tower could hit only one per turn.
- XSquare [§5.5 footnote]: turn order, RNG seeds and fixed direction-loop order can make a bot stronger on one side. **Always test both sides.**
- **Inference for UNSW:**
  - Head-on collisions kill both dragons, so the tile directly in front of an enemy head is the cat-path equivalent.
  - Because turns are sequential by id, a dragon that moves *after* an enemy head in the same round sees that head's new position. A dragon that moves *before* must predict it.
  - The id order is a real asymmetry, so a bot must reason about whether the enemy has already moved this round. This is the UNSW version of JWU's even-turn sync.

### 3c. Focus fire / trades / synchronised waves
- Big Red 2019: synchronised waves (a perpendicular line of prophets) beat streams of units.
- Om Nom 2025 discarded a high-variance rush at the last minute ("when it worked... extremely effective, when it didn't, almost guaranteed to lose").
- **Inference:** a head-on trade is 1-for-1 in dragons, but ⌈L/2⌉ pearls drop for each. Whether it is good depends on length and on who is nearer the drop. Score it explicitly rather than simply avoiding all head-ons.

---

## 4. Macro (economy vs aggression timing)
- **Rush is cheap and dominates early ladders, then fades.** Battlegaode 2020 wrote a rush on day 1 and was 2nd at Sprint. Bowl of Chowder 2020 quotes "1 hour coding a rush bot / top 10 within 2 hours". Smite 2019's rush was "obsolete in a day" after a rebalance.
- **Finals maps tend to reward economy.** Baby Ducks 2021 ("maps win games": slow, defensive finals maps). The Kragle 2025 said finals maps are historically larger and slower (UNVERIFIED as a general rule). Devs rate each tournament map for rush-friendliness and cap rush maps (Bowl of Chowder 2020, from devs).
- **Under-valuing economy is a recurring regret.** Battlegaode 2020 built its first compounding-income building in the last hour. The Kragle 2025's paper economics independently found the prize-winning meta.
- **Idle time is the silent killer.** Baby Ducks 2021: idle politicians charge after 10 turns with no enemy. Confused 2025: an idle timer sends units to the opposite side. The Kragle 2025: units spent half their lives travelling or waiting.
- **Self-adapting macro from cheap signals** instead of map classification (Baby Ducks 2021: target income rises with the number of living politicians). JBW 2020 used a "desperation index" that relaxes constraints after N failed turns.
- **Inference for UNSW:** the current meta (split into 2-segment children at rounds 0–3, peaking at 35–47 dragons) is the "economy of bodies" equilibrium. Do the paper maths first (Kragle method): the value of a length-2 dragon in pearl-collection rate vs its head-on risk, and when merging length into one long dragon wins the tiebreak ("longest dragon at round 500").

## 5. Exploration, symmetry, and deterministic shared computation
- **Symmetry detection:** known own base plus map edges gives the enemy base at one of 3 candidates (horizontal flip, vertical flip, 180° rotation) (Baby Ducks 2021, confused 2025, SPAARK 2025 symmetry message). The High Ground 2020's rush ran to the centre first to disambiguate the symmetry, which was "more consistent overall".
- **Cheap spreading:** JWU 2025 set each unit's initial explore direction to the side of the spawner it spawned on, and zig-zagged on diagonals to see more tiles.
- **Deterministic shared indexing / distributed decisions without messages** (Smite 2019): each castle independently computes which castle is closest to each target, and only that castle acts. Same inputs give the same answer everywhere.
- **Inference for UNSW (outdated since 2026-09-25: pearl schedules are now seeded per match):** pearl spawns used a fixed-seed RNG and were predictable on known maps (../unsw-game-analysis.md, engine source). Every dragon can compute the same future pearl schedule, including on tiles it can't see. The limit is that friendly positions outside 7×7 are unknown, so a global "dragon → pearl" assignment can't be recomputed exactly each turn. Workable versions:
  - **Territory by deterministic rule.** Each dragon owns a region derived from its id and spawn point, such as a Voronoi cell of the start positions or a strip, and farms the predicted spawns there. It needs zero comms and no knowledge of where friends are.
  - **Opening script.** At round 0 every dragon knows the full starting layout, so the whole team can compute one joint opening plan (for example the split timings and who goes where) and each dragon follows its own row. Divergence grows as enemies interfere, so switch to local rules after that.

  Smite-style "same inputs → same answer" is the single most transferable idea in this file. It only works on inputs every dragon actually shares.

## 6. Compute / bytecode budgeting
- Budget-check and bail out of expensive scans so you still act (JWU 2025). **Never exceed budget on a unit's first turn.** Fixing this gave Om Nom 2025 a "substantial win rate" gain, because the early snowball matters most.
- Avoid heavy std-lib containers in the hot path. `java.util` "will eat your bytecode" (Baby Ducks 2021). Bowl of Chowder 2020's own HashSet `add` cost about 65 bytecode vs about 161 for `java.util.HashSet`. SPAARK 2025 replaced `java.util.Random` (about 50 bytecode per call) with xorshift and used a bitset of explored tiles.
- **Code generation** (Jinja templates at Om Nom 2025, Python generators at Battlegaode 2020 and XSq) for unrolled loops. Om Nom also passed a **seeded RNG via makefile for reproducible A/B runs**.
- **Don't optimise early [XSq §5.12].** Correctness and structure come first, and bytecode work only later.
- **Inference for UNSW:** 100M points and C++ at ~60× cheaper than Python mean compute is rarely binding for grid-sized work in C++. The equivalent risks are (a) a Python bot, where a 32×32 flood fill costs 19M so only about 5 fit per turn, and (b) the first turn of a freshly split dragon doing expensive setup. Measure per-turn cost in the harness and alert at 50% of budget.

## 7. Code architecture that survives rebalances and rewrites
- Build strategy-agnostic primitives first (pathing, sensing, comms, micro), because "code that doesn't change when Teh Devs rebalance" (smite 2019; JBW 2020; Bowl of Chowder 2020; XSq §5.1).
- Per-turn pipeline: `sense → determineState → run state → state-invariant post-actions` (JWU 2025). Alternatives are a Strategy pattern with singleton behaviours that keep memory across switches (GST 2026), or a goal priority queue with `shouldStart/execute/shouldStop` (Kragle 2025, wished-for).
- Keep magic numbers in one constants file (The High Ground 2020 `MagicConstants.java`; XSq §5.11: use GameConstants).
- **Inference:** a single constants/weights file is also the natural surface for automated parameter tuning by the agent loop.

## 8. More recurring patterns (from the 2022–2026 and 2014–2021 postmortems; details in postmortems.md)

### Comms protocols suited to a narrow channel (one value a turn until 2026-09-24, up to four since)
- **Distance gradient by gossip** (wololo 2021, who went on to win 2022): each unit shows an approximate distance to the nearest target. Each round it adds 1, then caps the value at min(neighbour's shown value + distance to that neighbour). Any unit that sees the target directly uses the true distance. Explorers form "long chains which led them all towards one global target". *Inference:* this is the best fit for our sonar. Ping `dist-to-nearest-X` forward, and receivers take min(heard+Δ, own+1). It self-heals, needs no leader, and a spoofed low value only draws dragons toward one spot for a few rounds.
- **Outward-only relay** (food 2026 "resqueak"): re-broadcast a message only if you are *farther* from its source than the unit you heard it from. The message spreads out without echoing back. 3MiceWalkIntoABar planned a TTL mesh (messages with a hop limit) for the same reason.
- **Parity double-buffering** (don't @ me 2023): write on even rounds and read the odd section, then swap. Otherwise a message posted by a unit late in the turn order is wiped before anyone reads it. *Inference:* a UNSW sonar is read "at the start of the receiver's next turn". Whether a message sent by a higher-id dragon arrives in the same round or the next depends on the ids involved, so tag messages with round parity.
- **Coarse sectors and staleness** (4 Musketeers 2023): "enemy in sector 37" takes 7 bits instead of 12 bits for an exact location. Entries go stale after ~100 rounds, and a "claim" flag stops two units exploring the same sector.
- **Aggregation and hysteresis** (5 Musketeers 2022): average sightings into ≤3 running cluster centroids. A single enemy flickering at the edge of sensor range had made defenders run home and back forever.
- **Liveness heartbeats are fragile** (Lorem Ipsum and food 2026): a bit toggled every turn, where a missed toggle means dead. A unit that overran compute for one turn got marked dead. Use a multi-turn timeout.
- **Load-bearing comms** (Lorem Ipsum 2026): throttling squeaks "broke half the bot" because other logic silently depended on them. Document every consumer of a signal.

### Movement and cohesion using turn order
- **Follow-first grouping with zero comms** (don't @ me 2023): if a nearby ally moved this turn, follow it. If none did, you are the leader. *Inference:* with sequential ids, lower-id dragons naturally lead and higher-id dragons can copy their moves.
- **Spawn order as a formation tool** (Gone Fishin' 2023): spawn so that outer units move first, and inner units don't treat them as walls. That alone won ~2/3 of games against the same bot without it. *Inference:* a split child acts later in the same round (unsw-game-analysis.md), so choose split timing and direction with the move order in mind.
- **Randomised obstacle treatment breaks deadlocks** (Gone Fishin' 2023): treat friendly units as walls 75% of the time and as passable 25%. Losing leader-follower schemes ("follow the lowest-id leader" blocked the leader) were replaced by "move toward the healthiest ally nearest the front".
- **Temporary obstacles decay** (3MiceWalkIntoABar 2026): remembered unit positions sit in a cyclic queue and expire after 5 ticks. *Inference:* remembered snake bodies outside vision should expire at a rate set by their length (the tail moves every turn).

### Micro rules that recurred
- Always kite after attacking (Gone Fishin' 2023, corroborated by Super Cow Powers). Fight with local numbers, following Lanchester's square law (cout for clout 2024, crediting wololo's lecture). Retreat together so the healthy unit isn't left alone (4 Musketeers 2023).
- **Blind-spot geometry** (food 2026): an enemy cardinally adjacent to you can reach you next turn because you see at most 3 of its 5 destinations, so penalise cardinal adjacency. *Inference:* for head-ons, penalise any tile an enemy head can enter this round and weigh it by whether that head moves before or after you.
- **Team-strength state switch** (Gone Sharkin' 2024 `Micro.java`): compare nearby ally HP with enemy HP and switch between OFFENSIVE, HOLDING and DEFENSIVE. *Inference:* compare total nearby length and dragon count.
- **Measure the cost of passivity directly** (cout for clout 2024): a "47 vs 50 ducks" test showed that 3 static defenders lost 90% of games.
