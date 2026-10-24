# Transferable lessons for UNSW Battlecode 2026, ranked

**Context.** This is a swarm snake game on a torus: up to 64 dragons, each a separate process with 7×7 vision. The only communication is spoofable sonar (a forward uint32 on 2026-09-23; since 2026-09-24 up to four uint64 rays a turn, one per direction, with echo counts). Turns run sequentially by id. Head-on collisions kill both dragons.

The current meta (from 73 top-ladder replays) has four features:
- every top team splits into ~40 length-2–4 dragons early;
- most deaths are head-ons;
- nobody sprints and sonar is nearly unused;
- the #1 team wins mainly by dying less.

The timeline is ~4 weeks (Sprint 1 Oct, Quals 10 Oct, Final 17 Oct). The plan is an autonomous Claude-agent improvement loop.

**How to read this file.** Evidence points to sections in postmortems.md, techniques.md and process-and-tooling.md. "(inference)" marks my reasoning. Everything else is what the cited team reported.

## A. The improvement loop itself (highest leverage)

1. **Build an automated new-vs-old match runner first, and gate every change on it statistically.** Every 2023–2026 winner or runner-up had one:
   - Producing Perfection ran it in CI on every PR;
   - Gone Sharkin' ran ~75 maps × both sides;
   - Just Woke Up called it "monumental";
   - the 2026 winner ran 8 parallel workers and printed a binomial p-value;
   - 3MiceWalkIntoABar merged only at 95% confidence.

   Our matches take 0.5–10 s, so a few hundred games per candidate is cheap. The agent loop's accept/reject decision should be this statistic, not the agent's opinion.
2. **Play both sides of every map, and keep baseline seeds fixed.** Sides cause real bias from turn order, RNG and direction-loop order (XSquare guide §5.5). Every PP / camel_case / caterpillow runner swaps sides. In our game id order decides who moves first, so side bias is likely to be large (inference).
3. **Keep a diverse opponent pool, not just your own ancestors.** Evidence that self-play alone misleads:
   - JWU's final-winning feature tested neutral in self-play;
   - food and muskellunge both said self-play overfits;
   - The High Ground couldn't tune constants "given we could only play against bots we had";
   - Baby Ducks built a dedicated rush bot to spar against.

   **What to put in the pool:**
   - Re-implementations of the top ladder teams, built from their public replays (Big Red 2019 "reproduced Volatile's bot").
   - A pure rusher.
   - A turtle.
   - Frozen tournament snapshots, as caterpillow kept.
4. **Take the ladder seriously as the real test.** The top 3 in 2024 ran ~2.5× the median scrim count, mostly unranked ones they requested themselves (camel_case stats). JWU and the caterpillow 2025 team scripted automatic scrim requests against the top 15–20. Our ladder allows 60 manual games per hour via the JSON API. Script it to scrim the top ~10 on chosen maps and pull the replays back into the loop.
5. **Put replay analysis in the loop, not just win rates.** The most consistently cited high-return activity:
   - GST: "far more time reviewing scrims than writing code";
   - confused reached #1 mainly through replay analysis;
   - California Roll found an accidental emergent tactic in replays.

   Give the agent a replay → per-death classifier (head-on / body / kelp / self; who moved first; length at death), and have it propose fixes from the top loss causes. The fact that most deaths are head-ons makes this unusually tractable (inference).
6. **Change one thing at a time, and name every version after it.** XSquare §5.10: "Don't add too many upgrades at once". wololo kept ~65 feature-named snapshots and JWU shipped 57 versions in 25 days. For small effects, build targeted scenario maps rather than relying on the full pool (XSquare §5.10, food 2026: 3–5 purpose-built maps per behaviour, then a broad sweep).
7. **Write constraints down for the coding agent, and keep a human or strong reviewer on micro.**
   - The 2026 winner's `CLAUDE.md` files did this. The agent could only add strategy classes, had to keep refactors behaviour-identical, had to use the team's helpers, and had to clean "AI slop" before commit.
   - Lorem Ipsum's AI-written combat code lost almost every fight until it was rewritten, and his AI-written parameter tuner didn't work.
   - Agents are good at plumbing (runners, encode/decode, helpers). Micro judgement needs replay-grounded review.
8. **Expect the agent loop to fail the way CodeClash agents did, and design the harness around it.** In CodeClash (arXiv 2511.00839, which includes MIT Battlecode 2025 and BattleSnake arenas), LLM agents repeatedly edited bots between rounds. The best model (Claude Sonnet 4.5):
   - never beat an expert human-written bot;
   - validated its changes in only 56% of rounds;
   - made up loss causes in 46% of BattleSnake rounds after reading only the top of a log;
   - rarely recovered after a loss (<1/3 next-round win rate);
   - left its codebase increasingly messy.

   Countermeasures:
   - The harness forces A/B testing.
   - Loss analysis must cite specific turns from a structured replay summary.
   - Scheduled "pivot" branches try a different archetype through the same gate.
   - Dead code is pruned regularly.
9. **Search over named settings and discrete behaviours before raw code.** Evolved or genetic code failed outright:
   - Kraak 2024: 0 wins vs the example bot after 40×500 generations.
   - waffle 2026: one useful mutation, and the bot never qualified.
   - Lorem Ipsum's AI-written tuner "doesn't do well".

   Single scalar scoring formulas were easy to tune (Baby Ducks, SPAARK). Expose weights and toggles in one constants file (The High Ground `MagicConstants.java`). Let agents propose values and switches, and keep code edits for structural changes (process-and-tooling.md).
10. **Never ship untested, and never overrun compute on turn 1.**
   - smite 2019 and SPAARK 2023/24 lost tournaments to last-hour changes.
   - The 4 Musketeers lost as top seed to a turn-1 bytecode overrun.
   - Om Nom's fix for new units overrunning on their first turn gave a "substantial" win-rate gain.

   Here, a turn over budget kills the dragon, and a split child's first turn pays for its setup (unsw-game-analysis.md). Freeze the submission the day before each tournament, and alert when a harness turn uses more than 50% of budget.

## B. Strategy and tactics for a head-on swarm game

11. **Copy the current meta first, then improve on it. Don't invent from scratch.** The High Ground: in 6 years they won with an original strategy "exactly 0 times". XSquare: "All top teams … copy". confused, cout for clout, GST and Om Nom all name what they copied. Our replays are public, so the day-1 bot should reproduce the top team's split-early 40-dragon opening. Our edge should come from dying less (inference, matching the #1 team's profile).
12. **Micro beats macro in combat-heavy games.** Gone Fishin': micro +30–50% win rate vs macro +5%. cout for clout: copying XSquare's micro was their biggest jump. Here the micro is a single decision: whether to step into a tile an enemy head can reach. Invest there first (inference).
13. **Use XSquare-style exhaustive move scoring.** A dragon has ≤3 headings, optional sprint and optional split. Enumerate them all and score each with a feature record plus an ordered tie-break (techniques §3a). Put everything in one currency, as SPAARK's "5 score = 1 paint" did, so weights are tunable. Include an anti-oscillation penalty for recently visited tiles.
14. **Model turn order explicitly: it is the head-on weapon.**
    - Gone Fishin' won ~2/3 of games vs an identical bot purely by spawn-order formation.
    - JWU synced attacks to even turns.
    - don't @ me used a "follow the ally who moved first" rule.

    In our game a dragon moving after an enemy head this round sees where it went, and one moving before must predict it (inference). Children created by a split act later in the same round (unsw-game-analysis.md), which is another ordering lever.
15. **Treat a trade as a value calculation, not a reflex.**
    - Take trades when EV > cost (Baby Ducks' empower formula; confused's "fire only if value > cost").
    - Fight only with local numbers (the Lanchester square law behind cout for clout's squadrons).
    - Passive beats aggressive, but two passive swarms stall (food 2026, muskellunge 2024).

    A head-on is a 1-for-1 that drops ⌈L/2⌉ pearls for each dragon. Its value depends on the length difference and on who is nearer the drops (inference).
16. **Idle units lose games, so give every dragon a default job.** Baby Ducks (charge after 10 idle turns), confused (idle timer), The Kragle (half of unit life spent idle), cout for clout (3 static defenders out of 50 lost 90%). With 40+ tiny dragons, the default should be farming predicted pearl spawns in its own territory (inference).
17. **(Outdated 2026-09-25: pearl schedules are now seeded per match, so the schedule isn't predictable.)** **Exploit the predictable pearl schedule the way winners exploited shared deterministic computation.** Smite 2019 had every unit run the same BFS to get the same cluster list, and every castle compute the same "who's closest" answer. Here every dragon can replay the fixed-seed pearl RNG and derive the same territories or opening plan with zero comms (techniques §5). This is the one lever MIT teams rarely had and we do (inference).
18. **Keep the long-game win condition in view.** After round 500, the longest living dragon wins, then total length. Winners historically tuned their economy for slower finals maps (Baby Ducks: "maps win games"; Kragle's claim, UNVERIFIED as a general rule). Plan an explicit late-game switch, for example merging length into one protected dragon, and test it on the biggest maps (inference). The meta's longest dragon is only 10–23, so the bar is low.
19. **Adapt from cheap signals, not map classification.** Baby Ducks scaled their income target by the number of living politicians. food wished its unit-count thresholds had scaled with map size (a 12→16 change helped big maps and hurt small ones). Make split and aggression thresholds functions of map area and own/enemy dragon counts, not constants.

## C. Communication under spoofing

20. **Design the bot to work with zero comms, then add sonar as a bonus.** Battlegaode: "units would mostly make their own decisions". XSquare §5.9: independent behaviour beats coordination. JBW: "every time I'd try to implement a sophisticated strategy that requires a lot of coordination it would always flop". The meta ignores sonar and still wins.
21. **If you use sonar, authenticate it and never trust one message.** Replay-spoofing attacks were real (Prasici vs JBW in the 2020 finals). smite keyed its encryption on per-map features, and The High Ground changed keys right before submission. All our replays are public, so any fixed encoding will be read. Use a truncated keyed hash of (round, map) in the spare bits. Add hysteresis or aggregation too: 5 Musketeers' single flickering sighting caused an endless oscillation.
22. **Good first sonar uses** are relays that only spread outward (food's "resqueak" rule), a gradient that carries distance-to-target (wololo 2021), and parent–child pings right after a split, when the two dragons face apart. The child faces away from the parent (unsw-game-analysis.md), so check the geometry before relying on it (inference).

## D. Process and team habits for a 4-week sprint

23. **Build the durable primitives in week 1, then copy the meta onto them.** These survive rebalances: pathing over the static map, a pearl-schedule model, the move scorer and the match runner (smite 2019, JBW 2020, SPAARK 2025, Bowl of Chowder 2020). Don't bytecode-optimise early (XSquare §5.12). Here compute is abundant in C++, but not in Python, where one 32×32 flood fill costs 19M of the 100M-point budget.
24. **Upload early and use Sprint 1 as data, not a goal.** Om Nom: only after uploading did they see they were optimising the wrong things. The High Ground: "Sprint placement is meaningless", and they skipped it to study the meta. JBW lost Sprint round 1 and won the year. Sprints are for harvesting opponents' replays.
25. **Expect troll and odd maps, because the final is decided on them.**
    - confused lost the 2025 final on a sparse corner map;
    - Gone Fishin' lost the 2023 final on an odd island layout;
    - devs cap rush-friendly maps and add edge cases (Bowl of Chowder 2020).

    Build a deliberately weird map library: 10×10 and 64×64, kelp mazes, portal-heavy, sparse pearls, and asymmetric starts. The 2026 winner had 72 maps plus 20 scenario maps.
26. **Resist sophisticated coordination late.** Bowl of Chowder's 12-message "wall buster" made the qualifier bot worse. The High Ground's advice for the gap between quals and finals: make "one or two impactful improvements … very well".
27. **Don't discard a plausible upgrade on a near-50% result without checking for bugs** (XSquare §5.8). **Keep known-weird behaviour if removing it loses** (Om Nom and 4 Musketeers both kept a "bug" that tested better). The runner is the arbiter, not intuition.

## Out of scope / leads (for the whole mit-battlecode research set)
- **Not covered:** Sloppy Joe Pirates' 2026 postmortem (YouTube https://youtu.be/8PEjZTzAjSA). YouTube was unreachable and no transcript was available. Also not summarised: 2023 "no thoughts head empty", 2020 "confused" (HS), 2019 Oak's Last Disciple (used only for process), Double J 2019, and Cory Li's bytecode guide.
- **Code worth reading before building our harness and micro:**
  - the 2026 winner's repo https://github.com/awu7/battlecode-2026 (`autoscrims/`, `CLAUDE.md`, `maps/`, `test_maps/`);
  - camel_case's scripts https://github.com/jmerle/battlecode-2023/tree/master/scripts;
  - the Nudge runner https://github.com/outercloudstudio/nudge;
  - XSquare's public micro code, the most-copied artefact of 2022–2025;
  - Gone Sharkin' `gen_comms.py` in https://github.com/battlecode-archive/2024-Gone-Sharkin.
- **CodeClash** (https://arxiv.org/abs/2511.00839) is open source. Its BattleSnake arena is the closest public analogue to our game and could serve as a reference loop and a source of failure cases.
- **Lux AI (Kaggle)** reportedly saw RL/imitation-learning entries compete at the top. This is UNVERIFIED and was not researched, but it would be a useful contrast to Battlecode's "no ML" history.
- **Rules on AI assistance:** no official MIT rule was found (only Lorem Ipsum's statement that AI is permitted). UNSW 2026 allows AI use fully (organisers, reported by sig 2026-09-23).
- The **_drafts/** folder holds the helpers' raw drafts. Their content is merged into postmortems.md and process-and-tooling.md, so the folder can be deleted.
