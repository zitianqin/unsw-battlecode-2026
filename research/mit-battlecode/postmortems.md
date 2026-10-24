# MIT Battlecode postmortems: one section per team

There are 37 team sections: 20 from 2022–2026 and 17 from 2014–2021. Winners and top-4 teams come first where they exist. Each section has links, that year's game in one line, and 3–8 takeaways concrete enough to implement.

How to read this file:
- **Placements.** A placement marked "verified" was checked against https://battlecode.org/past.html. For 2023–2026 it was also checked against the final brackets on api.battlecode.org (see history-and-winners.md). "Self-reported" means only the team says so. UNVERIFIED is flagged inline.
- **Winners without postmortems.** Producing Perfection 2023, Gone Sharkin' 2024, wololo 2022 and subscribe to caterpillow 2026 published no postmortem, so their sections are built from their code repos.
- **Tags.** [COMMS] [MICRO] [TESTING] [META] [AI] tags and "Inference:" notes relate each point to UNSW Battlecode 2026.
- **Provenance.** Two research subagents gathered and wrote these sections; the lead agent assembled them. The lead spot-checked the 2026-winner repo (github.com/awu7/battlecode-2026: contributors awu7, naman-doshi, caterpillow; `CLAUDE.md` files and `autoscrims/ABtest.py` with a binomial p-value confirmed) and the 2023–2026 placements.

## Index

| Year | Team | Placement (as stated in section) |
|---|---|---|
| 2026 | Generalized Stroke's Theorem (UVA, 3 students) | 2nd overall |
| 2026 | Lorem Ipsum (Nuaym Syed, solo first-timer) | Finals qualifier, seeded 15/16, out after 2 matches |
| 2026 | food (Alex Thummalapalli, solo first-timer) | 9th–12th at Finals, 5th–6th US Quals, 10th seed |
| 2026 | 3MiceWalkIntoABar (4 freshmen) | Finals qualifier via US Quals, "knocked out rather quickly" at Finals |
| 2026 | subscribe to caterpillow (Naman Doshi, Alexander Wang, Andy Wu, Yiheng You) | 1st overall |
| 2025 | Just Woke Up | 1st overall |
| 2025 | confused (Michael Hahn, solo) | 2nd overall |
| 2025 | Om Nom (Cyril Sharma, Egor Gagushin) | 3rd overall, won US Qualifiers |
| 2025 | SPAARK (4 high-schoolers) | HS 1st |
| 2025 | The Kragle (RPI, 3 students) | ~27th, 10th-seed US university team; missed Finals |
| 2024 | cout for clout (4 high-schoolers, TJHSST) | HS 1st; 2nd on the final combined ladder |
| 2024 | muskellunge (David Teather et al., first-timer) | 13th overall, Finals 0–2 |
| 2024 | Gone Sharkin' | 1st overall |
| 2024 | camel_case (Jasper van Merle, solo) | 2nd overall |
| 2023 | Gone Fishin' (first-year team; Carl Guo, Ray Wang, Yuxuan Chen, Temo Toloraia) | 2nd overall, 1st seed into finals |
| 2023 | 4 Musketeers (CMU seniors, 3rd finals appearance) | 3rd overall |
| 2023 | Producing Perfection | 1st overall |
| 2023 | don't @ me | 7th |
| 2022 | 5 Musketeers (CMU juniors; same core team as 4 Musketeers 2023) | 7th overall |
| 2022 | wololo (Isaac Liao, solo) | 1st overall (past.html) |
| 2021 | Baby Ducks (Josh Brunner, Anthony Grebe, Jason Ye, Wesley Zhang) | 1st |
| 2021 | California Roll / "Chop Suey" (Stone Tao, solo) | 9th–12th per battlecode.org/past.html |
| 2021 | Malott Fat Cats (Ivan Geffner "XSquare", Jose Bastidas, Rodrigo Horruitiner, Ryan McDermott) | 4th; #1 on the ranked ladder for the entire second half of the competition |
| 2021 | wololo (Isaac Liao, solo) | 7th–8th per battlecode.org/past.html |
| 2021 | 3 Musketeers (Winston Cheung, Maxwell Jones, David Lyons, Bharath Sreenivas; CMU, first-timers) | 9th–12th per battlecode.org/past.html |
| 2021 | llllllll's Disciple (Ivy Zhang, solo, first year) | 3rd in High School tournament; 65th–128th overall |
| 2020 | Battlegaode (Hizami Anuar, Karen Gao, Anthony Grebe, Andrew Tockman) | 3rd in Final tournament; 2nd in Sprint |
| 2020 | Bowl of Chowder (Stone Tao, solo) | 5th–6th |
| 2020 | Java Best Waifu (Ivan Geffner "XSquare", Daniel Hopping) | 1st |
| 2020 | The High Ground (Eli Lifland, Aaron Ho, Alex Hoganson) | 4th |
| 2019 | smite (Vivek Myers, Mihir Patel, Nikhil Sardana, Vinjai Vale) | 1st |
| 2019 | Big Red Battlecode (Cornell team) | 8th |
| 2019 | NP-cgw (Jerry Mao, Angus Ritossa, Albert Smith) | Finalist |
| 2017 | Arbitrary Graph Restoration Fund (Aron Granberg, Simon Lindholm, Johan Sannemo, Mårten Wiman) | 1st |
| 2017 | test bot please ignore (Stuart Johnson "stuartj", Milo Hartsoe) | Top 32 of 450+ |
| 2016 | future perfect (Greg McGlynn, Luchang Jin) | 1st |
| 2015 | the other team (Greg McGlynn, solo) | 1st |

---

## Part 1: 2022–2026

#### 2025 — Just Woke Up — 1st overall (verified: battlecode.org/past.html label "1st Overall"; postmortem says they won the final)
- Link: https://battlecode.org/assets/files/postmortem-2025-just-woke-up.pdf
- Game: "Chromatic Conflict" — 2D Splatoon; paint 70% of map; soldiers/moppers/splashers + paint/money/defense towers; paint is both territory and ammo.
- Takeaways:
  1. **[TESTING] A/B harness over every map + auto-scrim script were the "monumental" workflow change.** They borrowed camel_case's (jmerle) script (https://github.com/jmerle/battlecode-2023) to run new-vs-old on every available map, then watched replays of the maps that flipped. They also wrote a script that logs into the site and auto-requests unranked scrims against ~top 20 teams, then reviewed every loss and every surprise win.
  2. **[TESTING] Self-play A/B can mislead; override it with judgment when a change targets opponents' styles.** Their resurrection feature and defense-tower feature were neutral-to-negative vs. old versions, but they shipped defense towers anyway for the finals because they targeted aggressive opponents (Confused, Om Nom) — and credit defense towers with winning the final. Lesson: keep a pool of *opponent-like* bots, not just your own ancestors.
  3. **Iteration volume: 57 bot versions in ~25 days (>2/day)**, each compared against older versions; bad changes branch from the previous version. "No one thing" won — "a bunch of tiny basic things extremely well".
  4. **Architecture: per-turn `sense → determineState → run state → state-invariant post-actions`** state machine per unit; Robot → Unit/Tower → per-type classes. They credit this with making new behaviours cheap to add. Inference: maps well to a per-dragon Python/TS process — sense grid, pick state (feed / hunt / flee / split), act.
  5. **[COMMS] Tight bit-packing of a 32-bit message: 3 bits type, 12 bits x/y, 2 bits payload**, via bit shifts; spare bits reserved. Directly analogous to our sonar values (uint32 then, uint64 since protocol 3 on 2026-09-24).
  6. **[MICRO] Kiting + synchronized attacks:** attack then step out of range so a tower hits you once per two of your hits; in finals they synced soldiers to enter tower range only on even turns so the tower could only hit one per turn. Inference: in a sequential-turn game, "whose turn is first" (id ordering) is exploitable the same way.
  7. **Exploration tricks:** initial explore direction = side of the spawner the unit spawned on (cheap spread-out); zig-zag diagonals to see more tiles at the same speed; "return location" memory to resume a task after refilling; bytecode-budget check to bail out of expensive scans and still act.
  8. **Tips (their TLDR):** start simple and upload early; steal automation scripts freely but only steal strategy code you understand (their copied XSquare exploration code hurt them in Sprint 1); watch replays of other teams — self-play only tests strategies you already thought of; don't burn out, stay consistent to the end.
- ML/LLM: none mentioned.

#### 2025 — confused (Michael Hahn, solo) — 2nd overall (verified: past.html "2nd Overall"; postmortem: lost final game 5 to Just Woke Up)
- Link: https://battlecode.org/assets/files/postmortem-2025-confused.pdf
- Game: same as above (Chromatic Conflict).
- Takeaways:
  1. **Solo, joined after Sprint 1, reached #1 on the scrim ladder by Qualifiers** with "no bytecode optimization" and simple algorithms. His stated recipe: "extensive replay analysis, borrowing strategies that looked good from other teams, and ... a well-defined core game philosophy." **[META]** He explicitly names the teams he copied (Asteroid's ruin-tainting, Gone Whalin's tower flickering).
  2. **Copying the meta fast pays off:** after watching the *other* region's qualifier, he adopted tower-flickering and got **70%+ win rate vs. his own qualifier submission**.
  3. **Write a game philosophy from replays and cut everything that doesn't serve it.** His two principles: towers first (e.g. don't paint under yourself before round 150 because it costs 5 paint to save 1/turn), and minimise unit idle time (default target = predicted enemy location by symmetry; randomness; an **idle timer** that redirects long-idle units to the opposite side of centre).
  4. **Value-threshold micro:** splasher scores each cell of a 7x7 window (enemy paint weighted much higher than empty, bonus near ruins) and only fires if the 3x3 sum > 50 = the attack's paint cost. Inference: "only act if expected value > cost" is the template for our split/eat/trade decisions.
  5. **Borrowed infrastructure:** camel_case 2024 code structure and pathfinding (unrolled Bellman-Ford, fall back to bugnav after 3 turns without progress), adapted by search-and-replace, with custom weights. Warns not to copy blindly.
  6. **Symmetry inference** from walls/ruins to locate enemy spawn, checking only the opposite half/quadrant to save compute; default to rotational and head to centre.
  7. **Loss analysis:** lost the deciding final game on a large, low-density map with ruins in corners because units defaulted toward centre — i.e. a map-type blind spot. Inference: test on deliberately weird maps (corners, sparse, huge).
  8. Scrims vs. finalists were ~50% — top of the ladder is a coin flip; map pool decides.
- ML/LLM: none.

#### 2025 — Om Nom (Cyril Sharma, Egor Gagushin) — 3rd overall, won US Qualifiers (verified: past.html "3rd Overall"; postmortem intro). Same team placed 5th (2023) and 4th (2024) per their own postmortem — UNVERIFIED elsewhere.
- Link: https://battlecode.org/assets/files/postmortem-2025-om-nom.pdf
- Game: Chromatic Conflict (2025).
- Takeaways:
  1. **Upload early.** They "floundered" week 1, didn't upload for days, and only after uploading realised they were optimising the wrong things.
  2. **Replay study found "obvious" fixes that mattered most:** units retreated to refuel at a fixed threshold (~50 paint) even when 10 attacks from killing a tower — making retreat conditional on what they're doing was a large win. Same for "finish the task before going home". Inference: in our game, "retreat/split when X" rules must consider current opportunity, not a fixed length threshold.
  3. **Don't exceed the compute budget on a unit's first turn.** New units allocated a big array and went over bytecode turn 1; fixing it gave a "substantial win rate against our old bot" because early snowball matters most. Inference: for 64 processes, startup cost of a freshly split dragon matters.
  4. **Derive hidden global state from cheap observables:** they inferred #paint towers from team money delta (max of last 10 turns' change → income = (20 + 3·SRPs)·moneyTowers, solved by lookup). Inference: in our game, analogous tricks may infer team size/alive count from whatever global info the API gives.
  5. **Pathing "Optimal Bug":** advance a bug-nav target a few steps treating units as passable, then BFS backwards from it within vision (bitmasks, ~1.5k bytecode) and pick the best BFS-decreasing neighbour by heuristic. Keeps bug's reach guarantee, handles unit clogging, cuts corners.
  6. **Code generation with Jinja templates** to unroll loops, inline "objects" into locals (avoid `this` loads), debug macros, and a seed passed via makefile for reproducible RNG. **[TESTING]** Seeded RNG = reproducible A/B runs.
  7. **[MICRO]** XSquare-style micro: compute stats per candidate square (can move, min enemy dist, damage dealt/taken, in tower radius) and choose by ordered tie-breakers.
  8. **[META] Copied Gone Whalin's tower-nuking after noticing in replays they recovered from losing towers.** Spent a long time reverse-engineering confused's rush and **discarded it last minute**: high variance ("when it worked... extremely effective, when it didn't, almost guaranteed to lose").
  9. Scrim results vs. top teams went from 0-10/1-9 in the morning to ~even after final-day submission; tournament maps favoured them (+100 Elo when tournament maps were uploaded). Inference: map pool heavily shapes outcomes; test on many maps.
- ML/LLM: none. Kept a bug that made money towers only build splashers because "fixing it somehow made everything worse" — empirical testing over intuition.

#### 2025 — SPAARK (4 high-schoolers) — HS 1st (verified: past.html "HS 1st"; they also placed HS 3rd 2024, 5th/6th 2023 per their postmortem)
- Links: https://battlecode.org/assets/files/postmortem-2025-spaark.pdf ; code https://github.com/erikji/battlecode25
- Game: Chromatic Conflict (2025).
- Takeaways:
  1. **[TESTING] `runmatches.py` lineage — the scripts are passed down:** SPAARK 2025 ← SPAARK 2024 ← 4 Musketeers 2023 `run_matches.py` ← Producing Perfection 2022. They ran **overnight batches** of new-vs-old and found this "actually very productive". Still: "Scrimmage analysis is still more important than raw win rate against old bots."
  2. **Never ship an untested last-minute change** — they threw in both 2023 and 2024 that way; in 2025 the testing script caught a bug with 5 minutes to spare.
  3. **Sequencing: build durable stuff first** (navigation, micro, code organisation, testing scripts, a set of test maps) because macro changes repeatedly; once the meta settles, copy the top teams' macro onto a solid base. **[META]** They abandoned their own "optimal" splasher rush after being 0-5'd, copied the top teams' "soldier build" (started at 25% win rate vs. their own bot, tuned until it won consistently).
  4. **[MICRO] Single-currency scoring:** everything converted to one unit (5 score = 1 paint). Movement score: +20 correct direction, +15 adjacent direction, −5/paint for tile penalty (x2 if you can't move next turn), −5/paint per adjacent ally, big penalty in enemy tower range (more if one-shottable), −20 in enemy mopper move+attack range, −1 for tiles visited < 5 turns ago (anti-oscillation). "Taking all the game constants at face value is enough to be competitive with top teams." **Must evaluate both move-then-act and act-then-move** — they missed this in 2023/2024.
  5. **[COMMS] Split the 32-bit message into two 16-bit records** (12 bits location, 3 bits type/team, 1 relay bit; an all-ones type = symmetry message). Index lookups via a coarse 12x12 grid (one ruin per 5x5 guaranteed) instead of linear search — linear search blew the bytecode budget when 20 messages arrived at once. Inference: our receivers will also need O(1) decode and a flood cap.
  6. **Congestion control:** after round 100 towers spawn only every 2 turns and only if <4 allies nearby; refill-waiters stand 2 tiles away. "Don't retreat unless you are close to the tower" beat old bots "by a very large margin".
  7. **Bytecode:** xorshift RNG instead of `java.util.Random` (~50 bytecode/call); bitset (60 longs) for explored tiles.
  8. **Process:** push WIP often, Discord task list for who's touching what.
- ML/LLM: none.

#### 2025 — The Kragle (RPI, 3 students) — ~27th, 10th-seed US university team; missed Finals (verified from their postmortem: "finished ... around 1800, ranked 27th"; "the only top 12 team not to make the finals"). Included as the mid-table "how we made the leap" view.
- Link: https://battlecode.org/assets/files/postmortem-2025-the-kragle.pdf ; code https://github.com/justinottesen/battlecode25
- Game: Chromatic Conflict (2025).
- Takeaways:
  1. **Do the economy math on paper first.** Tables comparing upgrade vs. new tower and paint-per-chip showed money-tower "disintegration" beats paint towers (10 vs 5 paint/turn equivalent) — independently discovering the prize-winning meta. Inference: before coding, spreadsheet our growth/split economics (pearl value per length, split cost, head-on trade value).
  2. **Idle time is the silent killer.** Soldiers spent ~half their lives travelling to/waiting at refill towers; other top teams simply let units die. Broadcasting the nearest "battlefront" to all units "massively" cut idle explore time. They only noticed because they finally watched opponents: "Other teams beat your bot for a reason, and the easiest way to improve is to copy what they do better than you."
  3. **Floating resources:** paint stranded in towers below the spawn threshold was a paper lead, not a real one. Inference: in our game, length held in one long dragon that can't convert it to kills/area is "floating".
  4. **Opening theory as its own module:** each tower spawns a soldier pair; on 90% of test maps they captured an uncontested ruin before turn 20; rush only if every ruin seen is contested. Gave them opening leads over higher-rated teams.
  5. **Goal priority queue framework** they wish they had: each goal type has `shouldStart / execute / shouldStop`; goals kept in a priority queue so a fight doesn't erase long-term memory.
  6. **Budget time:** they wasted time rewriting XSquare pathfinding they couldn't improve and on a SURVIVE behaviour that "never really impacted the result of any matches". Best tasks = best effort-to-result ratio, often hard-coding or greedy.
  7. **[TESTING]** Never wrote unit tests; recommend custom maps designed to expose problems, old-version gauntlets, scrims, and never uploading untested bots near deadlines. Scrim sample sizes they quote (7/13, 5/17) show how noisy head-to-head estimates are; 9th–16th were "separated more by auto-scrim luck than strength".
  8. Observation: organisers historically make finals maps larger/slower, favouring economy over rush (their claim; UNVERIFIED as a general rule).
- ML/LLM: none.

#### 2026 — Generalized Stroke's Theorem (UVA, 3 students) — 2nd overall (verified: past.html "2nd Overall"; lead's check of api.battlecode.org final bracket; postmortem congratulates subscribe to caterpillow on the win)
- Link: https://battlecode.org/assets/files/postmortem-2026-generalized-strokes-theorem.pdf
- Game: "Uneasy Alliances" — baby rats + 3x3 rat kings vs. neutral cats; win by killing all cats or all enemy kings; **directional vision cone**; dirt to dig; cheese economy; carrying/throwing ("ratnapping"); baby rats can't write the shared array, so local "squeaks" are the main unit-to-unit comms. 17,500 bytecode/turn.
- Takeaways:
  1. **[COMMS] Most relevant to our sonar:** local broadcast "squeaks" with a **3-bit type header**, each message type a class implementing a sealed interface (easy to add types, easy to debug). **"Squeak maximization"**: in combat every baby rat squeaked its HP, nearest enemy HP, direction and location every turn so allies could see behind their vision cone. Went over bytecode in crowded fights but judged "a very worthy tradeoff". Inference: our dragons are near-blind to anything not ahead/nearby, so a per-turn "here's what I see" ping is the analogous move — but ours is spoofable, so add a cheap authenticity check.
  2. **[META] Macro was copied, deliberately:** "queue scrims against teams above us ... reverse engineer their macro (much easier than reverse engineering micro...), copy ... if it made us better then we would keep it." Their king-formation location and "magic numbers" for how many units to build and when came this way.
  3. **[MICRO] XSquare-style micro** over 9 squares (incl. stay) with an if/else tie-break chain; enumerated **every valid move + turn + throw combination** and scored outcomes (hit cat / enemy / wall / ally / leaves vision). Some combos had to be banned empirically. Designed with a spreadsheet of factors.
  4. **Threat geometry:** the "cat path" (2–3 tiles directly in front of a cat) is the most lethal zone; avoiding it mattered but must not override enemy-avoidance. Inference: our analogue is "the tile directly ahead of an enemy head" for head-on collisions.
  5. **[TESTING/replays] "Best way to improve micro is to watch a lot of games"** — follow individual units into combat and fix each stupid decision with minimal side effects; watch better-micro teams (they studied Old But Gold). "I suspect we spent far more time reviewing scrims than actually writing code."
  6. **Pathing:** unrolled BFS + stack-based bug nav (from Gone Fishin' 2023), hand off to bug when BFS stops making progress; turn away from map edges; remember turn locations to break loops. Added a tunable dig cost. Cheap bit-shift reachability (~300 bytecode). Old BFS cost 6–8k bytecode.
  7. **Architecture:** Strategy pattern — a State Updater picks a singleton Behavior each turn (singletons keep memory across state switches). Avoided the 3000-line files of past years.
  8. **Regret:** lost a whole week between Sprint 1 and Sprint 2 to other commitments — "a massive blow". Against the "codegen spam" sentiment: macro, watching scrims, and trying many ideas matter more.
- ML/LLM: none mentioned in the postmortem.

#### 2026 — Lorem Ipsum (Nuaym Syed, solo first-timer) — Finals qualifier, seeded 15/16, out after 2 matches (verified: postmortem; lead's api.battlecode.org check says lost R1)
- Link: https://battlecode.org/assets/files/postmortem-2026-lorem-ipsum.pdf
- Game: Uneasy Alliances (2026) — see GST section. Comms: 64-int global array (0–1023 each) + local "squeak" of one 32-bit int, radius 4, once per turn; **cats can hear squeaks and pounce on the squeaker**.
- Takeaways:
  1. **[AI] Only postmortem found so far that describes AI-coding use in detail.** Large parts of early bots and "decently sized chunks" of the final bot were AI-written: pathfinding (bugnav, later one-step BFS), the Sprint-1 ratnapping/attack code, helper functions, and comms bit-packing encode/decode. Strategy and all debugging were his own. Outcome: the "vibecoded" attack micro "got destroyed in almost all head to head combat scenarios" until he rewrote it himself; the AI pathing had a bug (reset bug-nav state whenever a moving target shifted) that trapped units on maze maps until the finals. His **"vibecoded" parameter tuner was too slow and "did not work"**; never used. Inference: AI is fine for boilerplate (encode/decode, helpers); combat logic needs a human who watches replays.
  2. **[COMMS] Liveness via a toggling bit:** each king flips a "counter" bit every turn; a unit that sees it unchanged marks the king dead. Plus a "critical" bit for low HP. Failure mode: a king that runs out of compute for one turn gets marked dead. Inference: in our game, "silent for one turn = dead" is fragile; use a multi-turn timeout.
  3. **[COMMS] Squeaking is load-bearing and has a price.** Joint attacks used squeaks of target location/type/round; always-on squeaking got units killed by cats, so he cut it back — which then "broke half the bot" because king formation and ally-count checks relied on squeaks. Inference: document every consumer of a comms signal before throttling it.
  4. **Enemy memory with per-type staleness:** remember seen/heard units with timeouts (cats 5 rounds, rats 0, kings 10). Throw targeting by a weighted score (positive for enemy king/cat/rat, negative for allies) with ray-cast hit checks. Adding throws took him to >85% win rate in local tests.
  5. **[TESTING] Simple 3-step process:** (1) new vs. old on 1–3 maps where the change should matter; (2) full sweep over ~7 custom maps (from cat/cheese-heavy to very closed) + sprint maps — ~30% win rate means a bug, slightly >50% is accepted; (3) online scrims against similarly-rated teams, roll back if worse. Had one regular scrim partner team.
  6. **Scouting/specialised jobs died;** removing scout/soldier roles in favour of generalist miners raised survival (specialists got caught in 1v3s). Raising the cheese-carry cap 40→100 after studying another team gave "significant gains".
  7. Map-style dependence decided his qualification: weak in head-to-head combat maps, strong on open economy maps; won because 3 of 5 qualifier maps were low-interaction.
- Regrets: midterms, no time for the final bot; everything in one big file.

#### 2026 — food (Alex Thummalapalli, solo first-timer) — 9th–12th at Finals, 5th–6th US Quals, 10th seed (verified: author's own results section; lead's bracket check says lost R1 at Finals — consistent with 1 win in losers then out)
- Link: https://www.alext.app/Battlecode_Postmortem_2026.pdf (linked from battlecode.org/past.html)
- Game: Uneasy Alliances (2026).
- Takeaways:
  1. **Prep before kickoff:** read postmortems and past codebases for "a couple of days" before specs dropped — "exceptionally beneficial".
  2. **[COMMS] Relay flooding ("resqueak") with a distance rule:** a unit that hears an enemy-king location re-broadcasts it only if it is *farther* from the enemy king than the unit it heard it from — the signal spreads outward without echoing back. Units also squeak enemy locations they see, so allies facing away still enter combat. Rats stop squeaking when a cat can hear. Directly applicable to our sonar relay (four directions since protocol 3).
  3. **[COMMS] Heartbeat + SOS bitfields** in shared memory (toggle a per-king bit each turn; others clear a dead king's slot). Same bytecode-overrun race as Lorem Ipsum noted. Lossless location on small maps, lossy on big maps when 10 bits aren't enough. Inference: our sonar value must hold x/y on the torus — budget bits for coordinates first (64 bits since protocol 3, so there is room).
  4. **Few states:** 5 states, chosen by an ordered priority list (e.g. RETURN if carrying ≥40 cheese; coin-flip 50% COLLECT vs FIND_KING at spawn). Fewer states = easier debugging and more units doing the same global plan.
  5. **[MICRO] Blind-spot geometry:** an enemy standing cardinally adjacent can always reach and grab you next turn because you can see at most 3 of its 5 possible destinations — penalise cardinal adjacency. Score 9 moves; priority: position-based grab > HP-based grab > attack king > finishing blow > attack. Penalties halved when allies outnumber enemies 2:1; proximity penalties *inverted* near a king in SOS (bodyguard behaviour). Alternate move-then-turn / turn-then-move to scan sideways while travelling. Inference: our head-on collision analogue — compute which enemy heads can reach which tiles next turn given sequential id order.
  6. **Aggressive vs. passive balance:** passive beats aggressive, but two passive teams stall for hundreds of turns and lose map control; be aggressive only with numbers/position advantage. "Even if you end up losing rats ... your opponent will also, and then you can win with ... economy."
  7. **Parameter lesson:** raising min army from 12 to 16 improved big maps but hurt small combat maps — because on small maps you trade units faster and pay more per trade. Wish: scale unit-count thresholds with map size. "Many matches were won simply by who formed a rat king first."
  8. **[TESTING] Against overfitting:** accepting any change that beats your old bot overfits to your own bot and your maps. Running 20–30 maps at once hid *why* results changed; better: 3–5 purpose-built maps per behaviour, watch replays while iterating, then a broad sweep. Step through individual turns asking "what would I do here". Don't chase sprint results; early rush strategies rarely work by finals — build fundamentals.
- ML/LLM: none mentioned.

#### 2026 — 3MiceWalkIntoABar (4 freshmen) — Finals qualifier via US Quals, "knocked out rather quickly" at Finals (placement per lead's api.battlecode.org check: lost R1; exact final rank UNVERIFIED)
- Links: https://www.outercloud.dev/blogs/battlecode-2026/ ; bot https://github.com/outercloudstudio/battlecode-2026 ; runner https://github.com/outercloudstudio/nudge
- Game: Uneasy Alliances (2026).
- Takeaways:
  1. **[TESTING] Built "Nudge", a distributed game runner (Go), the most concrete harness in this set.** Trigger: "when testing locally, there was often enough local variation to make a bad change look as if it improved the bot." First prototype in one night; they **bisected recent commits by win rate**, found several regressions, and went from **<50% to ~70% vs. their benchmark** — just in time for Sprint 2. Cluster speed **1.2 games/s → 300–500 games in 5–15 min**.
  2. **[TESTING] Merge gate: every change on a branch + PR, merged only if the runner shows a win-rate gain at 95% confidence.** Inference: this is the single most copyable process for us — our games are cheap to simulate locally, and 64-process bots are noisy.
  3. Their own caveat: "Most of the teams in the finals did not use a game runner ... making actual fundamental improvements to your bot outstrips any benefit you gain by using a runner." They think they may have been the only finalist using one (their belief; UNVERIFIED). Their micro was the limiting factor and the runner didn't fix it.
  4. **Pathfinding:** BFS expanded 25 steps, obstacles bit-packed per direction into a char so expansion is a `switch` with no map reads; fallback bug nav. **Temporary obstacles** (units) in a cyclic queue that decays after 5 ticks.
  5. **[COMMS]** Squeaks for mines/enemies/cats; considered a **TTL-based mesh network** (messages relayed with time-to-live) but dropped it because squeaks attracted cats; after cats were nerfed they had no time to rebuild it. Inference: TTL relay is the natural design for our sonar.
  6. **Micro:** throw-direction weighting → movement selection → targeting; spacing rules between allies; an **"exhaust timer"** that pulls units out of combat if they haven't acted for N rounds (stuck attacking through walls).
  7. **[META] Watched top-team replays** (Super Cow Powers) and copied cheese-collection behaviour; noticed top teams didn't collect multiple cheeses and copied that without re-checking.
- **[AI] Explicit: "none of our code was written by AI"**; also avoided StringBuilder tricks and codegen on principle.

#### 2026 — subscribe to caterpillow (Naman Doshi, Alexander Wang, Andy Wu, Yiheng You) — 1st overall (verified: lead's api.battlecode.org final bracket; GST postmortem congratulates them on the win; UCalgary news https://arts.ucalgary.ca/news/ucalgary-students-win-big-mits-premier-programming-competition). **No postmortem found.** Evidence below is from their code repo.
- Repo: https://github.com/awu7/battlecode-2026 (branch `final`, last push 2026-02-04). Attribution: contributors are `awu7`, `naman-doshi`, `caterpillow` — the same three GitHub identities that authored the archived 2025 team repo https://github.com/battlecode-archive/2025-subscribe-to-caterpillow . I'm treating this as the team's 2026 repo (strong but indirect evidence; not labelled "1st place" in the repo itself).
- Game: Uneasy Alliances (2026).
- What the repo shows (facts from files; interpretation marked):
  1. **[AI] They used AI coding agents with written rules.** Each bot variant dir (`src/awubot`, `awubot_quals`, `awubot_us`, `awubot_sus`, `awubot_mystery`) has a `CLAUDE.md`: "when refactoring, always maintain 100% functionality the same as before"; extract repeated logic; strategy code expresses "decision logic only"; base robot classes hold no behaviour; **"AI-generated or exploratory code must be cleaned before commit"**; "Never edit `BabyRat.java`" — new behaviour only via new strategy classes; always use the team's `Pathfinder.makeMove` and bytecode-cheap `Util` wrappers instead of raw API calls. A file `how_to_optimise_bytecode.txt` is titled "top 10 six tips for optimising bytecode **for ChatGPT to read and not screw everything up**" (compute statically, precompute small-domain expressions into switches, switch on `.ordinal()` not enums, Jinja for unrolling). A commit reads "logically equivalent ai slop refactor". Inference: the winning team used LLMs as a constrained code-writer inside a human-designed architecture, with the architecture rules written down for the agent.
  2. **[TESTING] Parallel A/B harness with a significance test.** `autoscrims/ABtest.py`: runs TEAM_A vs TEAM_B on every map in a dir, both sides per map, `--reps N` repeats, fixed `--seed` option, 8 parallel workers by default, live `rich` table, saves replays per map/rep/seed, and reports a **two-sided binomial p-value** on the win count. A "skip list" drops troll/degenerate maps. Plus `run_all_maps.py`, `scrim_script.py` + `teams.json` (automated online scrims), and a `gen.py`.
  3. **[TESTING] Custom map library:** 72 `.map26` files in `maps/` + ~20 named scenario maps in `test_maps/` (e.g. `starvation`, `trapped`, `dirtpassageway`, `thunderdome`, `pipes`, `cheeseguardians`); commit "awubot gets stuck on testpathfinding2 in the 1 wide" shows purpose-built maps for regressions.
  4. **Process visible in commits (376 commits on `final`):** frequent "(untested)" / "(stable)" tags on commits, "will run tests with this current version overnight", "snapshot of current version for testing", "revert untested changes", "make it skip maps we have tested on". Frozen snapshots per tournament (`awubot_quals`, `awubot_us`) serve as baselines.
  5. **Bytecode notes kept as a file** (`bytecode_notes.txt`), e.g. `info.location` is 1 bytecode cheaper than `info.getLocation()`; enum switch is slow.
  6. Their 2025 repo (same team, qualified for 2025 finals per confused's postmortem) also contains `scrimmer.py`, which pulls the top-15 teams that auto-accept unranked scrims from the API and requests scrims on sampled maps — **automated scrim requests against the top of the ladder** (same idea as Just Woke Up's script).
- Unverified: their strategy details (no writeup found; GST's postmortem implies strong micro/macro but doesn't describe it).

#### 2024 — cout for clout (4 high-schoolers, TJHSST) — HS 1st; 2nd on the final combined ladder (verified: past.html "HS 1st"; ladder rank is their own claim with screenshot, UNVERIFIED independently)
- Links: https://battlecode.org/assets/files/postmortem-2024-cout-for-clout.pdf ; code https://github.com/ChiMasterBing/battlecode24
- Game: "Breadwars" (2024) — capture-the-flag with 50 ducks per side; ducks respawn from spawn zones; traps (stun/explosive), water to fill, attack/heal/build skill levels. Shared array readable/writable by all units.
- Takeaways:
  1. **[META] Stealing the best micro was their single biggest jump.** They copied XSquare's 2022 micro framework "in approximately 20 minutes" from his public GitHub and went **~1850 → 1950s → 2000+**. The framework: for each of the 9 possible moves compute a heuristic record, compare with a tie-break chain. Their earlier if-statement + combat-BFS micro "got too complicated to modify".
  2. **Controlled numbers tests:** "a simple test of 47 vs 50 ducks" showed tying up 3 units as static defenders lost **90%** of games — so they went full aggression (+130 rating, 1720→1850). Inference: in a 64-unit game, every idle/guarding unit costs a measurable amount; test it directly.
  3. **Lanchester's square law drives grouping:** squadron messages pull units to "combat locations" rather than straight to targets, because one extra unit adds more to a big army than a small one. Directly relevant to swarm-vs-swarm trades.
  4. **[MICRO] concrete rules:** move toward low-HP allies and low-HP enemies; step back if the ally behind has more HP; "chicken" mode below an HP threshold; don't clump so tightly you can't move; bucket behaviour by cooldowns and ranges.
  5. **[COMMS]** Message *queue* in the shared array (no slot clearing, saves 75 bytecode/write) layered on 4 Musketeers' buffer framework; top-priority distress message when the flag is grabbed makes nearby units swarm. Each unit's turn order is communicated at game start. Inference: our turn order by id is similarly exploitable.
  6. **Cheap sentinel trick (from Chenyx/Gone Sharkin'):** keep one unit unspawned and use `canSpawn()` to detect an enemy on the spawn pad — detection costs 1 unit instead of 3 static guards.
  7. **Process:** named packages per iteration; repeatedly tried then dropped strategies copied from top teams (level-6 builders tried twice, dropped after seeing the winner Gone Sharkin' didn't use it). "Most teams use the same ideas; it's the execution ... only possible through careful analysis of scrimmages and your code."
- ML/LLM: none.

#### 2024 — muskellunge (David Teather et al., first-timer) — 13th overall, Finals 0–2 (verified: past.html label "(13th)"; blog says 13th of ~400)
- Link: https://dteather.com/blogs/battlecode24/
- Game: Breadwars (2024) — duck capture-the-flag, 25K bytecode/turn, shared array for comms.
- Takeaways (brief — lower-signal source):
  1. **Self-play is a weak signal:** "if they're too similar they'll often draw a lot"; changes are better in some situations and worse in others. "At least half of the features I tried ... just ended up scrapping."
  2. **Scrim volume:** 818 unranked scrims (8th most of any team) and 1,294 total (7th most), all queued manually. Only at the end learned other teams used scripts to auto-queue scrims and evaluate changes (links jmerle's comparison script).
  3. **Watch your games and other teams' games**; steal and adapt strategies.
  4. **Regrets:** no code reviews/refactors → bugs and incomprehensible code; rushed for sprints ("sprints aren't everything"); used copied bugnav despite having bytecode budget for better pathing — lost sprint games stuck returning flags; two cautious swarms calculating they're at a disadvantage produce **standoffs** (same failure mode food reported in 2026).
- ML/LLM: none.

#### 2024 — Gone Sharkin' — 1st overall (verified: lead's api.battlecode.org final bracket; archive README says "1st place 2024"). **No postmortem found**; evidence from code.
- Repo: https://github.com/battlecode-archive/2024-Gone-Sharkin (main bot `src/bot1`)
- Game: Breadwars (2024).
- What the code shows:
  1. **[TESTING]** `compare_bots.py` is adapted from camel_case's `compare_full.py` (https://github.com/jmerle/battlecode-2023/blob/master/scripts/compare_full.py): runs both sides on **every map from every tournament so far** (default + sprint 1 + sprint 2 + quals + HS map lists, ~75 maps), 6 cores in parallel. Plus `copybot.py` to snapshot versions. ~25 bot dirs incl. named test bots (`rushbot`, `nothingbot`, `resignbot`, `pathing_test`, `nobuildertest`) — i.e. **purpose-built sparring bots** that each stress one behaviour.
  2. **[COMMS] Schema-driven comms codegen:** `gen_comms.py` takes a declarative schema (field name → slots, bit widths, e.g. `loc: 12`, `distress: 1`, `not_seen_cnt: 7`) and generates typed Java getters/setters. Lineage in its header: smite 2022 → maxwelljones14 2023 → Gone Sharkin' 2024. Inference: for our sonar (64-bit since protocol 3), a tiny schema → encode/decode generator (Python and TS) avoids hand bit-twiddling bugs.
  3. **[MICRO]** `Micro.java`: XSquare-style 9-direction `MicroDirection` records (`canAttack`, `canBeAttackedNext`, `canHealHigh`...), plus a team-strength comparison (sum of nearby ally HP vs enemy HP) that selects OFFENSIVE / HOLDING / DEFENSIVE state; micro loop stops when bytecode remaining < a constant. Attack → drop trap → move → attack → heal ordering.
  4. **Infrastructure:** `fast/` collection classes (FastLocSet, FastLocIntMap ...), `EnemyTracker`, `KillRecorder`, `RoleAssigner`, `SpecialtyManager`, codegen'd `BFS20` (~2,500 bytecode). Final commits: "Final: debug off", "Better pathing, aggressive on small map early", "Revert to spawn random" — map-size-dependent aggression.
  5. cout for clout's postmortem credits Chenyx (Gone Fishin'/Sharkin') with the unspawned-sentinel defence trick and notes Gone Sharkin' didn't use the level-6 builder meta.
- Directory names `camal_copy`, `camal_copy_new` mirror `bot1`'s file layout; whether they are a copy of camel_case's bot or their own is UNVERIFIED.

#### 2024 — camel_case (Jasper van Merle, solo) — 2nd overall (verified: lead's bracket check; README "2nd in the final tournament (16 participants)") + statistics site
- Links: https://github.com/jmerle/battlecode-2024 ; stats https://jmerle.github.io/battlecode-2024-statistics/ (linked from past.html); data https://github.com/jmerle/battlecode-2024-statistics/tree/master/data
- No written postmortem found. Code facts: `scripts/compare.py` (parallel, 6 threads, `rich` tables of per-map results and replay viewer links), `duplicate.py` (snapshot a version), `generate_bellman_ford.py` (codegen'd unrolled Bellman-Ford pathing, later reused by confused in 2025). **22 numbered versions** (`camel_case_v01` … `v21_final`), tournament submissions tagged in the name.
- Their scripts are the most-copied tooling in this whole set: Just Woke Up 2025 (A/B), Gone Sharkin' 2024 (compare_bots), confused 2025 (structure + pathing), SPAARK 2025 (bugnav).
- **Scrim-volume data I computed from the stats site's raw data** (non-tournament matches, 2024; 215 teams with scrims, median 487 per team):

  | Team (final place) | Total scrims | Rank by volume | Unranked | Ranked |
  |---|---|---|---|---|
  | Gone Sharkin' (1st) | 1,275 | 8th | 841 | 434 |
  | camel_case (2nd) | 1,304 | 6th | 816 | 488 |
  | dont eat my nonorientable shapes (3rd) | 1,156 | 14th | 639 | 517 |
  | om nom (4th) | 858 | 32nd | 446 | 412 |
  | cout for clout (HS 1st) | 1,246 | 10th | 810 | 436 |

  Takeaway: the top 3 all ran ~2.5x the median scrim volume, mostly **unranked** (self-requested testing). Caveat: counts are from jmerle's scraped snapshot; I didn't cross-check against the live API (muskellunge's self-reported 1,294 vs 1,290 here is close).

#### 2023 — Gone Fishin' (first-year team; Carl Guo, Ray Wang, Yuxuan Chen, Temo Toloraia) — 2nd overall, 1st seed into finals (verified: past.html "(2nd)"; lead's bracket check; postmortem congratulates Producing Perfection on winning)
- Link: https://battlecode.org/assets/files/postmortem-2023-gone-fishin.pdf
- Game: "Tempest" (2023) — HQs spawn carriers (mine wells) and launchers (combat); anchors capture islands (75% wins); clouds, currents, walls; 64-int shared array writable only near HQ/islands/amplifiers.
- Takeaways:
  1. **"A slightly improved macro strategy might increase our win rate by 5%, but micro can do a 30%–50% increase."** Learned "the hard way, tuning hyperparameters and playing against an older version of the bots a million times."
  2. **[MICRO] Always kite back after attacking** (launchers attack every turn, move every 2): attack 3 times per 4 turns vs. enemy's 2. They tried conditional kiting and found "ALWAYS kiting back ... is better" (corroborated by Super Cow Powers). Tie-break toward orthogonal over diagonal moves to keep formation.
  3. **[MICRO] Exploit turn order for formation:** units act in spawn order; spawning the first 4 launchers in a square with the *earliest* spawned furthest from base lets outer units move first so inner ones don't treat them as walls. This alone **won ~2/3 of games vs. the same bot without it**. Inference: our sequential-by-id turns make "who moves first" a first-class micro variable (especially for head-on collisions).
  4. **Target prioritisation:** for each enemy, count how many allies can hit it now and compute turns-to-kill; prefer one-shottable targets. Chase only when no immediate target, target is not a combat unit, or we're +2 units ahead.
  5. **Grouping without a leader:** "follow the lowest-id leader" failed (followers blocked the leader). Final: after a fight, move toward the ally that's closest to enemy HQ with highest HP; when adjacent, the lower-HP one stops (wounded stay behind).
  6. **Pathfinding lesson — "over-complicating things did not help":** A*/BFS and line-sweep bug didn't fit bytecode; winning version was stack-based bug nav + simulate both turn directions for N steps under a bytecode cutoff, never follow map edges, and **treat friendly units as walls 75% of the time, passable 25%** (randomness breaks loops). This stack bug nav was reused by GST in 2026.
  7. **Congestion control:** HQ raises a global "congested" flag when too many units nearby, halting spawns; carriers mark wells congested. Unit mix (mana vs adamantium carriers) chosen by map size via a rolling index counter reset every 100 turns.
  8. **Lost the final on odd maps**: their spiral scouting and "capture centre islands" bet were tuned for "mainstream" maps. Open question they raise: should you bet on the map-maker's trends? Also: they wrote everything in ~2,900 lines.
- ML/LLM: none.

#### 2023 — 4 Musketeers (CMU seniors, 3rd finals appearance) — 3rd overall (verified: past.html "(3rd)"; lead's bracket check)
- Link: https://battlecode.org/assets/files/postmortem-2023-4-musketeers.pdf
- Game: Tempest (2023) — comms only writable near HQ / owned island / amplifier.
- Takeaways:
  1. **[TESTING] Moved match-running off laptops onto GitHub Actions**, adapted from Producing Perfection: local runs against all past versions on >30 maps "can take hours and renders that person's computer useless". CI posts a match summary vs. a chosen older version. (See Producing Perfection section for the actual workflow.)
  2. **[COMMS] Sector system:** divide the map into small sectors; say "enemy HQ is in sector 37" (7 bits) instead of an exact location (12 bits); units go to the sector and resolve details with their own eyes. Global lists: combat sectors, mining sectors, explore sectors; entries go **stale after ~100 rounds**. Also "claim status" so only one unit explores a sector. Inference: coarse sectors were how to fit meaningful info into our 32 bits on a torus (64 bits since protocol 3 fit exact positions).
  3. **[COMMS] Store-and-forward local database:** when you can't broadcast right now, each unit keeps a lazy per-sector database of everything seen and flushes when in range. Writes batched through a "buffer pool" (read whole array locally, write local copy with dirty flags, flush dirty slots) because writes cost 100 bytecode. Bit-packing done by **auto-generated comms code** adapted from smite 2022 ("otherwise, changing something in the middle would create days worth of side effects").
  4. **Report danger immediately:** a carrier that meets an enemy runs home to report, because the next carrier will hit the same problem.
  5. **[MICRO] Retreat together:** if several allies are low HP and retreat to heal, the healthy one should go too — otherwise it's left alone and dies. Heal-spot scoring: in cloud (hidden), near allies, near self.
  6. **Pathing:** XSquare-style unrolled greedy BFS sized to remaining bytecode; bug nav upgraded to reason about obstacle endpoints ("points of interest") via double BFS spread over ~3 turns (~15,000 bytecode for a 20-wall obstacle).
  7. **Failure analysis:** lost as top seed in quals to the 16th seed due to (a) a turn-1 bytecode overrun on a weird 3-HQ-in-corner map that left HQ references null → a whole HQ useless; (b) units oscillating on a big wall map. Fix: more bytecode checks. Also a known bug left in because "fixing it actually made our bot worse".
  8. Team roles: one infrastructure person, one replay analyst ("finding a bad movement decision between rounds 134 and 135"), one data-structure/pathing person. Much code "copied or heavily inspired by others".
- ML/LLM: none.

#### 2023 — Producing Perfection — 1st overall (verified: lead's api.battlecode.org final bracket; Gone Fishin' postmortem congratulates them on winning). **No postmortem found**; evidence from code.
- Repos: https://github.com/battlecode-archive/2023-Producing-Perfection ; https://github.com/battlecode-archive/2022-Producing-Perfection
- What the code shows:
  1. **[TESTING] CI match runner (2022 and 2023):** `.github/workflows/main.yml` triggers on every **pull request** (and manual dispatch): builds, runs `run_matches.py`, uploads replays as artifacts, and **posts a markdown results table as a PR comment**. `run_matches.py` plays the current bot vs. a list of old versions on a list of maps, **both sides per map** (A/B swap to cancel spawn-side bias), records round count, resignations, and any `FLAG{...}` strings the bot prints (debug tags surfaced in the summary). This is the origin of the `run_matches.py` lineage SPAARK credits (Producing Perfection 2022 → 4 Musketeers 2023 → SPAARK 2024/2025).
  2. Bot versions are kept as named snapshots (`beforeAdjustments`, `beforeBetterMicro`, `sprintBot`) with a `copy_bot.py` script; baselines are named after what changed.
  3. The 2023 repo also includes a Rust tool to convert 2022 maps into 2023 format (`rust/bc23-mapconvert`) — reusing old maps as a bigger test set.
- Gone Fishin' notes Producing Perfection's island capture was "randomized", which beat Gone Fishin's centre-island bet on an unusual final map.
- Strategy details beyond that: UNVERIFIED (no writeup found).

#### 2023 — don't @ me — 7th (verified: past.html label "(7th)")
- Link: https://battlecode.org/assets/files/postmortem-2023-dont-at-me.pdf
- Game: Tempest (2023).
- Takeaways:
  1. **[MICRO] "Follow first" grouping with zero comms:** each launcher checks whether any nearby ally's position changed this turn; if so, it follows that ally; if none moved, it is the "first mover" and leads. Groups stay adjacent and win grouped trades against isolated units. They discovered the need by losing early 3v3s to teams whose launchers "stuck together". Plus **"explore on evens"**: all launchers move only on even turns outside combat so packs stay together. Inference: in a sequential-turn game, "copy the move of the ally who moved before you" is a free cohesion rule for our dragons.
  2. **[MICRO] Target value formula:** type value + HP damage dealt + missing HP/2 + 100 if it kills. Kite every time movement is available; "step up" instead of kiting if an ally is already engaged (enemy can only hit one of you).
  3. **[COMMS] Double-buffered commands by round parity:** write commands to the even section on even rounds and read the odd section, and vice versa — otherwise a unit late in turn order posts a command that gets wiped before anyone reads it. Counts per message type; wiping = reset count (no clearing cost). Directly relevant to our sequential-turn, broadcast-once world: messages need a lifetime of at least one full round.
  4. **Pathfinding trio:** unrolled Bellman-Ford (relax radially outward once or twice), greedy (preferred in combat / low bytecode / low vision), tangent bug as fallback. "In practice [greedy] often turned out to be more reliable."
  5. **Opening search pattern ("pinwheel"):** 4 initial units spawn at the 4 diagonals, go out 2, turn left and sweep a quarter each — fast coverage of the start area.
  6. **[TESTING] "Full-set scrimmaging":** 3 matches don't reveal strengths/weaknesses; use the 10-map online scrims + local all-map runs vs. old versions to track progress and the meta.
  7. **Advice:** "Code > Elo" — generalise instead of hard-coding for current ladder maps; this won tournament games against teams with hard-coded heuristics. "Make it work, make it right, make it fast." Copy top teams but "actively attempt to improve upon the 'inspired' algorithm". Create many packages/versions, one per idea.
- ML/LLM: none.

#### 2022 — 5 Musketeers (CMU juniors; same core team as 4 Musketeers 2023) — 7th overall (verified: past.html "(7th-8th)"; postmortem says 7th)
- Link: https://battlecode.org/assets/files/postmortem-2022-5-musketeers.pdf
- Game: "Mutation" (2022) — archons (bases) spawn miners/soldiers/builders; lead & gold; labs, sages, watchtowers; rubble slows movement; one 64x16-bit shared array writable by all.
- Takeaways:
  1. **[TESTING] Spreadsheet of every change vs. every old bot on every map, from day one** (they only did it before finals the year before). ~20 bots, one per major change; small changes tested against several recent versions, "not just one".
  2. **[COMMS] Enemy clustering instead of fixed targets:** units average the enemy positions they see into up to 3 running cluster centroids in shared memory (a far-away sighting starts a new cluster). Soldiers go to the nearest cluster. This fixed an **oscillation bug**: a single enemy flickering in and out of a base's sensor range made defenders run home and back forever. Inference: our sonar-based alerts need hysteresis / aggregation too, or one spoofed/flickering ping will yank the swarm around.
  3. **Token-ring turn order:** a shared counter incremented by each base at end of turn gives every unit its position in the turn order, so "first unit resets counters, last unit commits" survives deaths. Their original code assumed nobody died and needed a full redesign. Inference: with 64 independent dragons that die and split, any "leader" role must be re-derivable from live state each turn.
  4. **Resource allocation across producers** sharing one global pool: prioritise producers near enemy clusters; rotate among ties; each declares what it intends to build so others can compute leftover.
  5. **Healing starvation:** "heal the lowest HP first" left units waiting 1,000+ rounds while newly injured units cut the queue — a dozen units out of action lost the mid-map fight. Fix: heal cap + timeout; "more advantageous for them to just go into the fight". Found by reviewing replays.
  6. **Balance patches reset the meta** (gold cost cut → sage spam within hours); be ready to rebuild strategy mid-competition.
- ML/LLM: none.

#### 2022 — wololo (Isaac Liao, solo) — 1st overall (verified: battlecode.org/past.html lists wololo / Isaac Liao as 2022 winner; api.battlecode.org has no bc22 bracket; no 2022 postmortem found). Evidence: code + his 2021 postmortem.
- Links: code https://github.com/battlecode-archive/2022-wololo ; 2021 postmortem (7th that year) https://battlecode.org/assets/files/postmortem-2021-wololo.pdf
- What the 2022 repo shows:
  1. **Many small, feature-named, numbered snapshots:** 26 bots in `sprint_1/` (`micro_5`, `retreat_20`, `targeting_23`, `tunnel_pathing_22`, `mining_25` ...), 19 in `sprint_2/`, 21 in `src/` (`sage_micro_14`, `fury_fix_20`, `starting_soldiers_17` ...) — **~65 versions**, each named after the one thing it changed, plus test bots like `suicide_test_16`, `mine_adjacent_test_17`, `spreadtest_1`. Same pattern as Just Woke Up's 57 versions.
  2. **Python codegen for pathing** (`write_pathfinding_algorithm_18.py` generates unrolled per-offset distance code for an 11x11 window).
- From his 2021 postmortem (relevant technique, older year): **distributed distance-field via flags** — each unit displays an approximate distance to the nearest target feature; each round it increments its value, then caps it at min(neighbour's displayed value + distance to that neighbour); anything that directly sees the feature caps at the true distance. Explorers relaying targets formed "long chains which led them all towards one global target". Inference: this gossip/gradient protocol is a strong fit for our sonar — each dragon's ping can carry "distance to nearest food/enemy cluster", and the swarm self-organises without a leader. cout for clout 2024 also credits "Wololo's lecture" on Lanchester's laws for their grouping logic.

---

### Cross-cutting patterns (2022–2026)

1. **Every recent winner or runner-up had an automated new-vs-old match runner, and the scripts get passed from team to team.** Examples: Producing Perfection (1st 2023) ran matches in CI on every PR and posted results as a PR comment; Gone Sharkin' (1st 2024) ran ~75 maps × 2 sides in parallel; camel_case (2nd 2024) wrote `compare.py`; Just Woke Up (1st 2025) called A/B testing "monumental"; subscribe to caterpillow (1st 2026) ran 8 parallel workers with a binomial p-value. The scripts are handed down: PP 2022 → 4 Musketeers 2023 → SPAARK 2024/25, and camel_case → Gone Sharkin'/JWU. Details that recur: always play both sides of each map (cancels spawn-side bias), fixed seeds, save replays per game, and a significance gate (3MiceWalkIntoABar merged only at 95% confidence; caterpillow printed p-values). *Sources: PP, Gone Sharkin', camel_case, JWU, caterpillow, SPAARK, 4 Musketeers, 3Mice, 5 Musketeers (spreadsheet).*
2. **Beating your own old versions is necessary but not enough; the winners also scrim hard against real opponents.** JWU shipped the defense towers that won them the final even though self-play A/B showed them neutral. food and muskellunge describe how self-play overfits. In the 2024 data I computed, the top 3 each played about 2.5x the median number of scrims, mostly unranked ones they requested themselves. JWU and caterpillow (2025) wrote scripts that auto-request scrims against the top ~15–20 teams. *Sources: JWU, food, muskellunge, camel_case stats, caterpillow scrimmer.py, don't @ me ("full-set scrimmaging"), Kragle.*
3. **Watching replays is the highest-return activity, especially following single units through a fight.** GST: "we spent far more time reviewing scrims than actually writing code." confused reached #1 on the ladder with no bytecode tricks, mainly through replay analysis. Om Nom's biggest fixes (retreat rules, paint efficiency) were "obvious" once they watched. The 4 Musketeers found bugs down to a single round ("between rounds 134 and 135"). *Sources: GST, confused, Om Nom, food, 4 Musketeers, 5 Musketeers, cout for clout.*
4. **Copy the meta fast, and say so.** Nearly every top postmortem names the team it copied. Examples: cout for clout took XSquare's micro and went from ~1850 to 1950s, then past 2000 with follow-up fixes; confused took Gone Whalin's tower flickering and won 70%+ vs. his own previous bot; Om Nom also took flickering; GST copied macro "magic numbers" from teams above them. The Kragle's main regret was *not* copying ("Other teams beat your bot for a reason"). The caveat is to copy only what you understand, then improve it. JWU's copied exploration code hurt them, and Om Nom reverse-engineered a rush and then threw it away because it was too high-variance. *Sources: cout for clout, confused, Om Nom, GST, Kragle, JWU, SPAARK, don't @ me.*
5. **Micro beats macro in combat-heavy games, and one micro framework dominates: XSquare-style.** Score each of the 9 possible moves (8 directions plus staying put) on a record of features (can attack, can be attacked next turn, distance to enemies, ally support), then pick one with an ordered tie-break chain. Recurring rules:
   - always kite after attacking;
   - evaluate both move-then-act and act-then-move;
   - prefer targets you can finish off;
   - fight only with a numbers advantage (Lanchester's square law);
   - wounded units drop behind;
   - avoid the tiles an enemy can reach next turn.

   Gone Fishin' put it as micro = +30–50% win rate vs. macro = +5%. *Sources: Gone Fishin', cout for clout, Gone Sharkin' Micro.java, GST, food, don't @ me, 4 Musketeers, SPAARK, Om Nom.*
6. **Turn order is a weapon.** Units act in a fixed order, and top teams exploit it:
   - Gone Fishin' spawned units so the outer ones move first, which won about 2/3 of games vs. the same bot without it.
   - JWU timed synchronized attacks to even turns.
   - don't @ me had each unit follow whichever ally moved first this turn, and double-buffered messages by round parity so late movers' messages aren't wiped before anyone reads them.
   - cout for clout shared each unit's move order at game start.

   *Inference:* our game runs turns sequentially by id and head-on trades dominate, so modelling "which enemy head moves before me" belongs in our core micro. *Sources: Gone Fishin', JWU, don't @ me, cout for clout, 5 Musketeers (token-ring turn order).*
7. **Comms are bit-packed, schema-generated, coarse, and time-limited.** Common patterns:
   - a 3-bit type header (JWU, GST, SPAARK);
   - coarse sectors instead of exact coordinates (4 Musketeers);
   - declarative schema → generated encode/decode code (smite → 4 Musketeers → Gone Sharkin');
   - stale-after-N-rounds and liveness heartbeats (food, Lorem Ipsum);
   - relay floods that only spread outward (food's "resqueak") or carry a time-to-live (3Mice's plan);
   - a distributed distance-gradient (wololo 2021).

   Two failure modes recur: messages that flicker (5 Musketeers' oscillation), and hidden dependencies that break when you throttle comms (Lorem Ipsum). *Sources as listed.* Note: none of these years had spoofable enemy-readable comms, so authentication is a gap we have to design ourselves.
8. **Many small, named versions, and never ship untested.** Examples: JWU 57 versions in 25 days; wololo ~65 feature-named snapshots; camel_case 22 versions; caterpillow tags commits "(untested)"/"(stable)". SPAARK and the 4 Musketeers lost tournaments to last-minute or turn-1 bugs, and Om Nom fixed a turn-1 bytecode overrun for a big gain. *Sources: JWU, wololo repo, camel_case repo, caterpillow repo, SPAARK, 4 Musketeers, Om Nom.*
9. **Maps decide close matches, so build a deliberately weird map library.** Examples:
   - confused lost the final on a sparse map with resources in the corners;
   - Gone Fishin' lost on an odd island layout;
   - Om Nom gained +100 Elo when the tournament maps were released;
   - Lorem Ipsum qualified because 3 of 5 maps were low-interaction.

   Winners keep dozens of custom maps: caterpillow had 72 maps plus ~20 scenario maps; Gone Sharkin' ran every past tournament map. food recommends 3–5 purpose-built maps per behaviour while iterating, then a broad sweep. *Sources as listed.*
10. **AI coding use is now visible at the top, and it's constrained.** The 2026 winner's repo has a `CLAUDE.md` in each bot folder. It limits the agent to adding strategy classes, preserving behaviour on refactors, cleaning AI code before commit, and using the team's bytecode-cheap helpers. They also kept a bytecode-tips file written "for ChatGPT to read". Lorem Ipsum (a solo Finals qualifier) used AI for pathing, comms encoding and early combat. The AI combat code lost almost every head-to-head fight until he rewrote it, and his AI-written parameter tuner didn't work. 3MiceWalkIntoABar used no AI at all. No postmortem reports using ML to train bots. *Sources: caterpillow repo, Lorem Ipsum, 3Mice.* *Inference:* use agents for plumbing (encode/decode, runners, helpers) inside rules a human writes, and keep a human in the loop on micro through replays.

---

### Out of scope / leads
- **Sloppy Joe Pirates (2026)** postmortem is a YouTube video (https://youtu.be/8PEjZTzAjSA). YouTube was unreachable from this machine (DNS failure), so I have no transcript or description. Not covered.
- **XSquare "Guide to Battlecode"** (https://battlecode.org/assets/files/battlecode-guide-xsquare.pdf): skipped per lead (lead is reading it). XSquare's micro is the most-copied single artifact in this set.
- **2023 "no thoughts head empty" (Newbie 2nd)** postmortem (https://battlecode.org/assets/files/postmortem-2023-no-thoughts.pdf, ~1.6k words): downloaded, not summarised.
- **Other 2026 code worth a look for micro:** Old But Gold (GST studied their micro heavily) https://github.com/battlecode-archive/2026-Old-But-Gold ; SPAARK 2026 (97 .py files, likely tooling) https://github.com/battlecode-archive/2026-SPAARK ; Goobie Woobies (6th per their repo description, UNVERIFIED) https://github.com/daannte/bc26 ; 3Mice's runner Nudge https://github.com/outercloudstudio/nudge.
- **Om Nom's "Optimal Bug" pathing and Jinja codegen** repo (linked from their PDF). GST plans to adopt it.
- **2024 stats data** (https://github.com/jmerle/battlecode-2024-statistics/tree/master/data) and 2023 equivalents could support deeper analysis (e.g. scrim volume vs. final rank across all 16 finalists).
- **Older postmortems** (2019–2021) on past.html, incl. Baby Ducks 2021 (1st) and Java Best Waifu 2020 (1st): out of the 2022–2026 scope.
- Absence of CLAUDE.md/AGENTS.md in the other 2026 archive repos I scanned (ProofOfConcept, SPAARK, Gravy, Powerpuff Girls, Complex Merlin, TSPAARK, Old But Gold, Goobie Woobies, podemice, 3Mice) does **not** show they didn't use AI. Archive repos may contain only submissions.


---

## Part 2: 2014–2021

#### 2020 — Battlegaode (Hizami Anuar, Karen Gao, Anthony Grebe, Andrew Tockman) — 3rd in Final tournament; 2nd in Sprint (self-reported)
- Source: https://web.archive.org/web/2023/http://web.mit.edu/agrebe/www/battlecode/20/index.html (live web.mit.edu URL returned HTTP 500 on 2026-09-23)
- Game: "Soup" 2020 — landscapers bury enemy HQ in dirt or outlast it against a rising flood; comms via a global paid "blockchain" of 7 msgs × 7 ints per round, sender not recorded.
- Takeaways (facts from source):
  1. **Comms minimalism.** Because messages cost soup and are unauthenticated, they only broadcast HQ locations and a few coordination signals; "units would mostly make their own decisions ... based on the positions of nearby units." Units were spawned where their role was obvious from local context. (Inference: directly applicable to spoofable sonar — design dragons to be locally sensible without messages.)
  2. **Local-grid pathfinding under a budget.** Building the adjacency graph cost ~100 bytecode/tile, so they only examined a 5×5 grid, then ran a Bellman-Ford-style relaxation seeded with (distance-to-target × 100), stopped after 3 iterations. Stuck detection (remember last few positions) falls back to bug navigation.
  3. **Loop unrolling via code generation.** Replacing a 2-D array with individual variables and unrolled loops cut bytecode cost >2×; the ~1000-line unrolled Java was generated by a Python script, which was "easier to debug and optimize" than hand edits.
  4. **Rush first, on day one.** They implemented a miner rush on day 1; rushes are easy to write and hard to defend, so they dominate early (2nd at Sprint with a "fairly simple rush strategy"). By the qualifying tournament top teams had economy + drone swarms and the rush weakened.
  5. **Hard-coded positions break on unseen maps.** Sprint bug: design school hard-coded "4 units south of HQ" worked on the dev test maps and failed on many tournament maps. Lesson: test across many map geometries.
  6. **Single-file code became unmanageable;** they refactored to OO structure after Sprint.
  7. **Late realisation of economy.** Built their first vaporator (the compounding-income building) in the final hour before the deadline — they regret under-valuing economy. The eventual winners out-econ'd them ("Java Best Waifu ... had a sufficiently large economy").
  8. **Scrimmage leakage trade-off:** scrims pause before later tournaments to avoid leaking strategies, "but in earlier rounds the benefit of testing your strategy usually outweighs the disadvantage of leaking it." They also say they under-weighted the seeding tournament.

#### 2021 — Baby Ducks (Josh Brunner, Anthony Grebe, Jason Ye, Wesley Zhang) — 1st (verified: past.html; 2nd in Sprint 1 and Sprint 2 self-reported)
- Source: https://web.archive.org/web/2023/http://web.mit.edu/agrebe/www/battlecode/21/index.html
- Game: "Campaign" 2021 — Enlightenment Centers spawn politicians/slanderers/muckrakers (rock-paper-scissors); comms via a 24-bit per-robot flag readable by nearby units; maps offset by a random constant so units don't know map bounds.
- Takeaways (facts from source):
  1. **Build two bots and spar them against each other.** Week 1 they wrote a muckraker-rush bot and a slanderer-economy bot and "tested both against each other extensively to improve both. Having a good rush bot to test against greatly helped us develop our defense." (Inference: in an agent loop, maintain a deliberately aggressive sparring bot, not just the previous version of yourself.)
  2. **Explicit, tunable scoring functions for micro.** Politicians empower only if `100*(kills-2) + (influenceDrained - convictionSpent) > 0` (muckraker kills ×2, EC kills ×10). A single scalar formula is easy to tune.
  3. **Self-adapting macro from cheap signals.** Target income rises with the number of living politicians (more alive = they aren't finding fights = defensive map). Avoids explicit map classification.
  4. **Relay comms for local threats.** Enemy-muckraker sightings passed robot-to-robot so slanderers flee threats beyond their own vision. Symmetry inference: known own EC + map edges → enemy EC is at one of 3 locations (reflect H, reflect V, rotate 180).
  5. **Bytecode: avoid `java.util`.** "java.util.* will eat your bytecode"; hand-written heap helped; Bellman-Ford with 1–2 unrolled sweeps beat Dijkstra in practice because it's easier to unroll.
  6. **Idle units must go do something.** Politicians hovering "defending" were changed to charge the nearest known threat or enemy base if no enemy seen in 10 turns — big improvement in converting economy to wins.
  7. **Spec changes mid-competition matter.** The devs rebalanced several times (slanderer ROI capped, muckraker buff made linear); strategies must be re-validated after each patch.
  8. **"Maps win games."** Finals maps were slow/defensive, favouring their economy strategy; their late build-order overhaul (keep building income on defensive maps) was decisive. They had essentially no pathfinding until week 3.

#### 2021 — California Roll / "Chop Suey" (Stone Tao, solo) — 9th–12th per battlecode.org/past.html (author says 9th) + "Adaptive Strategy" award
- Source: https://blog.stoneztao.com/posts/bc21/ ; code https://github.com/StoneT2000/Battlecode2021/
- Game: 2021 (same as Baby Ducks above).
- Takeaways (facts from source):
  1. **Bots are curated decision trees, not ML.** "Every single bot submitted to Battlecode (that performs well) is effectively a giant, carefully curated, grown, and pruned, decision tree ... it's usually near impossible to use advanced AI algorithms."
  2. **Copy the meta.** Copying other top bots' early build queues made him "instantly shot back up" after a nerf. "Do not assume early, copy more, read specs carefully, flesh out features on specs before code."
  3. **Replay analysis finds emergent strategies.** "Muck flanking" was something his bot did by accident; spotting it in replays and hard-coding it took him to #2 on the ranked ladder. "Careful analysis of match replays is crucial."
  4. **Don't dismiss pathfinding early.** He decided in week 1 pathfinding didn't matter (no walls, only passability); greedy pathing was "a lot worse than I anticipated", causing a week and a half of unnoticed pathing problems and slow map control.
  5. **Comms bit-packing.** Discord taught him map-relative coordinates fit in 14 bits (64×64 map with a large random offset → use coord mod 128), doubling comms efficiency.
  6. **Read the spec before coding.** Misreading specs led to discarded code and dead code/unused comm signals left in the final bot.
  7. **Community/Discord is a strategy source** (ideas, meta discussion). Stats: final bot 2,908 LOC, 191 commits, max 23 commits/day.
- Prior placements he states: 5th in 2020 (Bowl of Chowder), 9th in 2019 (Codelympians). (Self-reported.)

#### 2020 — Bowl of Chowder (Stone Tao, solo) — 5th–6th (verified at battlecode.org/past.html)
- Source: https://stonet2000.github.io/battlecode/2020/
- Game: 2020 "Soup" (see Battlegaode above).
- Takeaways (facts from source):
  1. **Infrastructure first.** "Working on infrastructure first is always much more beneficial and building a working, robust bot, is a priority." Quotes Smite that the code that survives rebalances is "pathfinding, resource gathering, basic communications, and functional attacking code."
  2. **Rush is cheap and strong early.** Quote from Anomalous Pandas: "we spend a week coding a bot and we're like 20th place / we spend 1 hour coding a rush bot / top 10 within 2 hours." Rush later turned out to be "truly dependent on map RNG".
  3. **Devs curate tournament maps against dominant metas.** Devs told finalists they rated each tournament map for rush-friendliness and capped rush maps per round; "troll" edge-case maps appear every tournament. Many bots broke on HQ-in-corner maps. "Find all edge cases and make sure they don't crash your project."
  4. **BFS by precomputed delta table.** A generator script emitted all (dx,dy) offsets in BFS order into a constant array; scanning vision = iterate the array, nearest-first, no queue/visited set.
  5. **Custom collections.** His own HashSet `add` cost ~65 bytecode vs ~161 for `java.util.HashSet`.
  6. **Ambitious coordination features backfire late.** A 12-message "drone wall buster" introduced bugs that made the qualifiers bot worse. Reflection: "I should've spent more time tuning my bot instead of attempting to make a drone wall buster."
  7. **Comms can be hacked.** In finals, Prasici spoofed Steam Locomotive's comms to mislead them about their own HQ location, so their landscapers didn't defend. (Directly relevant to our spoofable sonar.)
  8. **Backup bots:** he copy-pasted bot versions into separate folders as backup/fallback bots.

#### 2020 — Java Best Waifu (Ivan Geffner "XSquare", Daniel Hopping) — 1st (Final tournament champion)
- Source: https://battlecode.org/assets/files/postmortem-2020-java-best-waifu.pdf (placement stated in PDF and in lead's reading of battlecode.org/past.html)
- Author history (self-reported): Potato 13–16th 2015; Felix and The Buggers 9–12th 2016; Oak's Disciples 3rd 2017, 4th 2018; Oak's Last Disciple 33–48th 2019.
- Game: 2020 "Soup".
- Takeaways (facts from source):
  1. **Strategy-agnostic primitives first.** Week 1: "soup gathering, navigation, communication, an easily modifiable building order" — BugNav used all tournament. They played zero scrims before Sprint and lost round 1, but used Sprint to observe the top strategies (turtle, rush, lattice) and then adopted the best idea (lattice) plus drone harass.
  2. **Bytecode trick: walk a direction sequence.** Instead of offset arrays, hard-code the sequence of directions that visits every visible cell; `loc.add(dir)` is 2–3 bytecode vs >15 for `new MapLocation(x+X[i], y+Y[i])` — saved >2000 bytecode/turn.
  3. **Compact broadcast of a computed plan.** HQ spends spare bytecode computing the wall via BFS, then broadcasts HQ location + a bitmask (bit i = i-th visible tile is interior). Every unit derives the same tile classification locally. (Inference: one message → shared deterministic plan computed identically by every agent; ideal for no-shared-memory dragons.)
  4. **Priority lists for edge cases** (9-level tile priority for landscapers) and a "desperation index" (turns spent unable to place a building → relax placement constraints at thresholds 8 and 30). Avoids hard-coded positions that break on troll maps.
  5. **Shared threat map from broadcasts.** Each drone kept a map-sized array; each reported net gun adds +1 to cells within range, each destroyed one −1; >0 = obstacle. Time-scheduled phases (crunch at turn 1400/1550, second attack 2200).
  6. **Counter-specific managers.** A `RushManager` took over unit production when rushed; matched enemy landscaper count, timed drones for when enemy likely lacked soup for a net gun.
  7. **Comms spoofing threat was real.** Prasici replayed JBW's messages from previous games to confuse it; failed only because they replayed at the wrong round. "If instead of copying those messages they had tried to deduce the pattern ... they would have succeeded since we didn't actually encrypt our messages that much."
  8. **Advice:** "Simplicity, Robustness and Structured Code ... every time I'd try to implement a sophisticated strategy that requires a lot of coordination it would always flop." Tracked per-opponent win rates (The High Ground 50%, Kryptonite 40%, Smite ~65–70%). "If the code undergoes drastic changes ... do a bot from scratch."

#### 2020 — The High Ground (Eli Lifland, Aaron Ho, Alex Hoganson) — 4th (also #1 on scrimmage ladder before quals; 1 seed in finals)
- Source: https://battlecode.org/assets/files/postmortem-2020-the-high-ground.pdf
- Game: 2020 "Soup".
- Takeaways (facts from source):
  1. **Skipped Sprint, watched the meta.** Didn't submit for Sprint; used its results to classify strategies (rush/turtle, turtle, terraform/attack).
  2. **Build a rush bot as a sparring partner.** Night before seeding they coded a rush bot ("we would eventually want a good rush bot to test against anyway") and woke up #2 on the ladder. Rushing miner first ran to map centre to determine which of 3 symmetries applied — slower in the best case, "more consistent overall".
  3. **Shameless copying from replays.** "We decided to shamelessly copy Java's strategy ... pulled up some replays of Java's games and worked on a new bot for several days until we imitated it as closely as possible", then tweaked (cleaner build order via desired-composition ratio → soup priority). This got them to #1 on the ladder.
  4. **Tuning failed for lack of opponents.** Constants lived in `MagicConstants.java`; "it was tough to tune them robustly given we could only play against bots we had ... we were constantly making big changes to our bot, so it felt like any tuning would soon be possibly irrelevant." (Inference: an automated loop with a diverse opponent pool addresses exactly this gap.)
  5. **Secrecy costs testing.** They didn't scrim their last-minute anti-Java turtle against Java or Smite to avoid leaking it, so opponent-specific bugs went undiscovered. They changed their comms "encryption constants right before we submitted", which protected them from Prasici's replay-spoofing.
  6. **Ignored cows → lost.** Lost to Smite 3–2 with both losses on cow-heavy maps; "not dealing with an important aspect of the game" came back to haunt them.
  7. **Lessons from 6 years (their ranking):** (a) "Don't be afraid to copy ... in all our years we have [won with an original strategy] exactly 0 times"; (b) prioritisation via a task tracker ranked by expected win-rate gain, not fixing everything seen in a replay; (c) general code (nav, resources, comms) first; (d) Sprint placement is meaningless; (e) write extensible code (a 5 AM drone-class rewrite cost them); (f) use visual indicator lines/dots for debugging instead of prints; (g) between quals and finals, make "one or two impactful improvements ... very well" — in 2019 they were too cautious (fell from top team to 4th), in 2020 too scattershot.

#### 2019 — smite (Vivek Myers, Mihir Patel, Nikhil Sardana, Vinjai Vale) — 1st (verified at https://battlecode.org/past.html; the PDF itself was written 2019-02-01 *before* finals and reports 3rd in the Qualifying tournament)
- Source: https://battlecode.org/assets/files/postmortem-2019-smite.pdf
- Game: 2019 "Crusade" (JavaScript/Python/Java transpiled) — castles, pilgrims mine karbonite/fuel, churches, crusaders/prophets/preachers; comms via paid radius signals (16-bit) and 8-bit castletalk; fuel spent on moving/attacking.
- Takeaways (facts from source):
  1. **Foundation first:** "you start with pathfinding, resource gathering, basic communications, and functional attacking code. Code that doesn't change when Teh Devs rebalance."
  2. **Rewrite fast when the meta shifts.** After a post-Sprint rebalance made rushes "obsolete in a day", they tore down the Sprint bot and rebuilt the economy bot in the 48 hours before seeding.
  3. **Deterministic shared indexing to compress comms.** Every unit runs the same BFS over resource tiles to compute the same ordered list of clusters (<32), so a location of interest is a 5-bit cluster index instead of 12-bit x,y. Zone downsampling (256 zones) fits a location in 8 bits. (Inference: with a narrow sonar, have all dragons derive the same map landmarks deterministically so messages carry indices; 64-bit values since protocol 3 make this optional.)
  4. **Distributed decisions without messages.** Each castle independently computes which castle is closest to each target cluster; only the closest one produces a harasser, the others skip a turn. Same inputs → same answer everywhere.
  5. **Untested last-hour features lose games.** A preacher rush added "an hour before the deadline" with little testing caused almost all their seeding-tournament map losses (bug: the left-behind pilgrim always mined fuel).
  6. **Scrims as bug finder:** "every single loss could be traced to a major bug, and we found bugs in most of our wins too." Pre-finals scrim record 141–33, ≥60% win rate vs every finalist open to scrims.
  7. **Anti-spoofing:** every signal read/write wrapped in encrypt/decrypt keyed on per-map features (fuel layout, map size), so signals differ every game and recorded scrim signals can't be replayed. (Directly relevant to spoofable sonar.)
  8. **Copy opponents' good ideas** (dense lattice, aggressive churches from Gisthekey) and add scouting to detect and react to specific enemy strategies.

#### 2019 — Big Red Battlecode (Cornell team) — 8th (self-reported; Top 32 in 2018)
- Source: https://battlecode.org/assets/files/postmortem-2019-big-red-battlecode.pdf
- Game: 2019 "Crusade" (see smite above). Each unit its own process (like our dragons); per-unit time limit 100 ms + 20 ms/turn.
- Takeaways (facts from source):
  1. **Reproduce the top bot and test against it.** "We reproduced Volatile's bot and ran our bot against it in order to better build out the defensive economy strategy." "Scrimmage data can also only get you so far ... it's necessary to reproduce other bots."
  2. **"Build a working bot before writing a complex one (and build one fast)" — the goal is "an initial bot that's improvable."** (Cites stuartj's 2017 postmortem for the same idea.)
  3. **Scrimmage constantly:** "theorycrafting can only get you so far"; they ran lots of scrims right before and after major tournaments to sample the meta.
  4. **Rock-paper-scissors metas.** Early 2019 had a 3-way equilibrium (preacher rush / prophet rush / preacher defence); later econ vs unit-heavy. Teams over-specialised in econ lost to "now-rogue" rushes on small maps. (Inference: our evaluation pool must keep old "dead" strategies like rushes to avoid regressions.)
  5. **Engineering hygiene:** modular classes, version control, exception handling so you can swap strategies on meta change. Community tools matter ("so many teams ... swear by Clairvoyance", a community replay viewer).
  6. **Synchronised waves** of units beat streams: moving prophets as a perpendicular line so more fire at once.

#### 2021 — Malott Fat Cats (Ivan Geffner "XSquare", Jose Bastidas, Rodrigo Horruitiner, Ryan McDermott) — 4th; #1 on the ranked ladder for the entire second half of the competition
- Source: https://battlecode.org/assets/files/postmortem-2021-malott-fat-cats.pdf
- Game: 2021 "Campaign" (see Baby Ducks). Comms = 24-bit flag per unit per turn.
- Takeaways (facts from source):
  1. **Build the spec-stable parts first; skip exploits you expect to be patched.** Knowing the initial specs were broken, they built comms and pathfinding in week 1, and deliberately didn't implement self-empower because they predicted a nerf (correct, but it cost them Sprint 2 vs Kryptonite).
  2. **Comms protocol for a tiny channel.** Each unit *cycles* through a fixed list of facts (map bounds, EC IDs/teams, EC x, EC y, neutral EC influence), one per turn, plus priority "emergency" messages (enemy muckraker seen). Units track what their parent EC already knows and prioritise unknown facts. Coordinates sent mod 128 (2× max map size) and reconstructed as the nearest match to own coordinate. (Inference: very close to our situation — then a 32-bit sonar per turn, up to four 64-bit ones since protocol 3; gossip facts round-robin with priority interrupts.)
  3. **Micro as a scored search over options.** Politicians evaluate every attack radius: health restored to allies + damage to enemies + 17 per kill/conversion; act only if efficiency > own health. Enemy unit type inferred from its max-health value (optimal slanderer values are a known set of 30).
  4. **Code-generated pathfinding.** A restricted Dijkstra (only paths moving away from origin) computed as a fixed recurrence; they generated a 3000-line method with 400 if-statements, no loops — ~6000 bytecode to evaluate all paths in vision.
  5. **Weighted build order.** Replaced a fixed cycle with per-unit weights (always build the unit with lowest accumulated weight; weights vary over time) — easy to tune.
  6. **Evaluate against old versions — and beware your map pool.** "We couldn't get a considerable winrate against our old bot by only changing build order"; final econ/offence balance was chosen as "the most winrate against our old bots on the maps used for Sprint and Sprint 2." That backfired: "Relying on previous maps turned out to be a pretty bad idea" — finals maps were huge/impassable, and they lost two 2–3 series. (Inference: self-play on a stale map pool overfits; generate diverse maps.)
  7. **Emergent behaviour noticed empirically:** muckrakers that go to the closest EC wasted potential, so they sent them along the map borders for 150 turns first ("muckraker flanking").

#### 2021 — wololo (Isaac Liao, solo) — 7th–8th per battlecode.org/past.html (tied with Nikola); wololo then WON 2022
- Source: https://battlecode.org/assets/files/postmortem-2021-wololo.pdf ; code https://github.com/iliao2345/Battlecode2021
- Game: 2021 "Campaign". The only finalist built primarily on a muckraker rush.
- Takeaways (facts from source):
  1. **Distributed Bellman-Ford via flags (gossip gradients).** Each unit shows its approximate distance to the nearest feature on its flag; each round it increments its value, then caps it at (neighbour's shown distance + distance to neighbour); if it sees the feature it caps at the true distance. Used for exploration (features = areas empty of friendly explorers) and routing units to a global target through chains. (Inference: this maps almost exactly onto 64 dragons with a per-turn sonar value — a hop-count/distance gradient needs only a few bits.)
  2. **Micro step then macro step.** Micro: minimax over own move directions vs the largest nearby enemy politician's best empower radius (computed twice then combined with cumulative maxes — cheap). Ties go to macro, which picks a direction by the unit's role + greedy pathing. Clean layering: immediate tactical safety first, strategy as tie-breaker.
  3. **Comms encoding:** first bits = message type header; coordinates mod 128; large quantities sent as a rescaled logarithm; each unit's flag also advertises its role and a "get out of the way" bit.
  4. **Bytecode:** reversed for loop `for (int i = n; --i >= 0;)` is the cheapest loop; custom circular linked "integer cycler" replaces java.util sets.
  5. **Testing via other teams' auto-accept scrims.** Being underranked after a rules change, he tested by requesting scrims against top teams that had auto-accept on; thanks monky and 3 Musketeers for leaving it on — "I tested my strategies against them very often." Many top teams turned auto-accept off.
  6. **Contextual strategy switching as a late hedge.** In the final half-week he added a turtle mode and let ECs switch rush↔turtle by context (map size/passability), which held up against turtles that had beaten his pure rush. Before quals he declined a major change as "untested, and too risky."
  7. **Unseen map features blindside everyone:** almost all finals maps put ECs on low-passability tiles, which no previous map had done.

#### 2021 — 3 Musketeers (Winston Cheung, Maxwell Jones, David Lyons, Bharath Sreenivas; CMU, first-timers) — 9th–12th per battlecode.org/past.html (author says 9th of 16)
- Source: https://battlecode.org/assets/files/postmortem-2021-musketeers.pdf
- Game: 2021 "Campaign".
- Takeaways (facts from source):
  1. **Priority-ordered control flow beats ad-hoc state switching.** Their EC state machine with a "stateStack" grew to >100 entries and wasted turns; they rewrote it as one prioritised if-chain decided at the top of the turn (stack max ~11). Hindsight: put the decision in a method and return early.
  2. **Bit-packed typed messages.** An `InformationCategory` enum header, dx/dy from home in 7+7 bits, log-scaled influence.
  3. **Bytecode:** a `StringBuilder`-backed int set (`FastIterableIntSet`) because StringBuilder ops cost ~1 bytecode in the engine — used for fast sets/maps.
  4. **Adaptive bidding by binary search** on opponent's bid (raise low bound on loss, lower high bound on win, hold X when X wins and X−1 loses). (Inference: generic pattern for learning a hidden opponent parameter online within one game.)
  5. **Turn-order exploitation:** units act in creation order, so a "rush buddy" built immediately before the main politician empowers first and clears diluting units. (Relevant: our game has sequential turns by id.)
  6. **Use deaths as information:** send a buff muckraker to where the first scout died (probably near enemy base).
  7. **Formations > individualism:** copied lattices from top teams; monky's lattice treated units as repelling point charges.
  8. **Advice:** steal ideas; read old postmortems/code; "watch playthroughs of matches either against other teams or against old versions of your bot ... We found a lot of bugs by going over a bunch of games frame by frame (even in wins)"; re-read the spec and javadocs. Complaint: finals maps differed sharply from scrim maps.

#### 2021 — llllllll's Disciple (Ivy Zhang, solo, first year) — 3rd in High School tournament; 65th–128th overall (self-reported)
- Source: https://ivyzhang.me/bc21-postmortem
- Lower placement; included for two sharp lessons.
- Takeaways (facts from source):
  1. **Basics dominate.** A 30-minute greedy pathfinder (pick the highest-passability of the 3 directions toward target) gave "an 100% win rate" against her previous bot.
  2. **Last-minute submission bug:** cleanup code meant to run "past Turn 900" only ran *on* turn 900; she went 0–2 and last in quals. (Inference: every submission needs an automated regression gate vs the previous version.)
  3. **Lossy comms:** bucketing EC influence in steps of 50 to fit location + influence in 24 bits — "sacrificing accuracy for speed of communication."
  4. **Map pool decides greed vs aggression:** her less-greedy bot beat greedier eco bots on small maps with many neutral ECs.

#### 2019 — NP-cgw (Jerry Mao, Angus Ritossa, Albert Smith) — Finalist (per https://battlecode.org/past.html; exact finals placement UNVERIFIED — post ends "Finals tournament: To be continued… maybe?")
- Source: https://jerrymao.net/projects/battlecode/ . Author history (self-reported): wcgw 7th–8th 2017, wcgw 2nd 2018.
- Game: 2019 "Crusade" (see smite).
- Takeaways (facts from source):
  1. **Sabotage enemy comms channels.** Trading could be abused as 2×10-bit shared memory between castles, so their castles accepted any unpayable trade offer, wiping the opponent's trade "cache." (Inference: expect opponents to probe and poison any shared channel — our sonar is exactly such a channel.)
  2. **Local RPS testing.** They built a rush bot and a defence bot and played them against each other: Turtle < Rush < Defence < Turtle; they shipped a merge (defend 30 turns, then turtle).
  3. **Group selection with one number.** Castles broadcast a minimum robot ID; all units with ID ≥ it join the swarm — addresses a random-ish subset with no ambiguity. (Inference: with id-ordered turns and sonar, ID thresholds/ranges are cheap addressing.)
  4. **Tech debt forced a restart.** ~2000 lines of "strategy, patches, and if statements" whose later patches broke earlier invariants (churches didn't broadcast what castles read) caused a seeding bug; they restarted the whole bot before quals and qualified without losing a match.
  5. **Copy observed coordination.** Watching scrims vs Oak's Last Disciple revealed centralised castle assignment of pilgrims to depots; they adopted it.
  6. **Timed concave via shrinking circle.** Attack = a clock-driven circle around enemy castles (radius r(t) shrinking fast then slow); units only advance if outside the circle, so they arrive simultaneously in a concave. Deterministic time-based coordination without messages.
  7. **Late additions untested:** Church saber added "in the final hour ... not perfected or thoroughly tested"; early-game resource contest stayed underdeveloped because chasing meta/bugs ate priorities.

#### 2015 — the other team (Greg McGlynn, solo) — 1st (verified: https://battlecode.org/past.html). Also won 2014 as "that one team" and 2016 as "future perfect" (past.html)
- Source: https://web.archive.org/web/2020/http://www.anatid.net/2015/12/battlecode-2015-post-mortem.html (original anatid.net is down)
- Game: 2015 — HQ + towers, ore mining, drones/launchers/missiles; comms via 65,536 shared broadcast channels (effectively shared memory).
- Takeaways (facts from source):
  1. **"Frequently scrim against the top teams and copy their strategies. To do well in Battlecode you don't need to invent clever strategies yourself."** The strongest unit changed after each nerf (drones → launchers); teams that didn't adopt it lost.
  2. **"Reliable execution" separates top teams:** "every robot was doing something useful ... units didn't get stuck while pathfinding ... didn't sit around waiting forever for some attack signal that never came ... if they lost some units, they rebuilt them." Therefore "simple strategies are better than complicated ones" — his launchers: (1) shoot nearest enemy, (2) else go to nearest enemy tower. Complex group-up/retreat logic adds failure modes (never reaching 5 units, oscillating attack/defend).
  3. **Distributed computation on idle units.** A BFS "pathfinding supercomputer" ran on 30 miners' spare bytecode, state stored in the shared broadcast array; recompute when a unit hits an unknown obstacle. Won a best-pathfinding prize. (Not directly portable — our dragons share no memory — but "use idle compute of many agents" is.)
  4. **Rock-paper-scissors indecision** between three of his own strategies before seeding; he picked one by scrims.
  5. **Missed innovations late:** "scouting missiles" (fire beyond vision to trigger barrages) appeared 1–2 days before deadline; he didn't implement and lost launcher duels in finals. Economy stayed "really stupid" (always 30 miners, mine closest ore) because deviations showed no big improvement.

#### 2016 — future perfect (Greg McGlynn, Luchang Jin) — 1st (verified: https://battlecode.org/past.html)
- Sources: https://web.archive.org/web/2020/http://www.anatid.net/2016/02/battlecode-2016-post-mortem-future.html ; code + README https://github.com/TheDuck314/battlecode2016
- Game: 2016 "Zombie Invasion" — archons, soldiers/turrets/scouts/vipers, zombies spawn from dens; no shared vision; only scouts/archons broadcast (64-bit messages, cost grows with range). First year of "blind and mute" robots.
- Takeaways (facts from source):
  1. **Imitation bots as sparring partners (the clearest example of this practice).** The `src/` directory keeps many dated versions, several "named after strategies we saw other teams using": `supercowpowers*` (imitation of Super Cow Powers, became the ancestor of the final bot), `simplesoldier18` (imitation of The Simple Soldier's zombie-pulling), `felix15` (imitation of Felix & The Buggers' soldier/viper aggression, "a frequent test opponent"), turtle bots `ttm_heal`, `ttm_dense_9`. Version names encode the January date.
  2. **Preparing for a scary strategy pays off.** After losing one scrim to The Simple Soldier's zombie-luring, they implemented it themselves, tested against it, and learned to build only soldiers under heavy zombie attack — which helped beat "foundation", who used that strategy in finals.
  3. **Wasted work on counters for a fading meta:** the anti-turtle charge was never used because turtles disappeared. (Inference: build counters only when the strategy appears in the current opponent pool.)
  4. **Practice-map mismatch:** Sprint maps had far more zombies than practice maps; they had no archon-retreat code and went out in round of 8.
  5. **Comms as an attack surface:** team 1064CBread spammed hundreds of broadcasts so enemies burned their compute reading the shared signal queue (patched by the devs). Their own messages: first 4 bits = channel number; `Messages.sendRadarData` packs up to 5 enemy types+locations in one signal.
  6. **Bytecode idioms** (from the same author, https://web.archive.org/web/2020/http://www.anatid.net/2015/12/battlecode-idioms.html): make everything static (saves the `this` push); precompute lookup tables, and store big tables in String literals (no static-initialiser cost; e.g. sqrt/random via `charAt` ~5 bytecode vs 20–75); avoid java.util, use big pre-allocated arrays; switchable debug "indicator" sets.

#### 2017 — Arbitrary Graph Restoration Fund (Aron Granberg, Simon Lindholm, Johan Sannemo, Mårten Wiman) — 1st (verified: https://battlecode.org/past.html); same core team won 2018 as Orbitary Graph
- Source: https://github.com/HalfVoxel/battlecode2017 (README only; no written postmortem found)
- Takeaway (fact): **Local version-vs-version tournament harness.** `./run -a versionA -b versionB --tournament` plays two versions "in all maps"; `./backup versionTag` snapshots the current bot as a named version. This is the minimum viable eval loop, built by a two-time champion.
- 2018 note (fact, from https://github.com/mvpatel2000/battlecode-2018-smite README, Smite tied 9th): Orbitary Graph "stunned the tournament" with "flying mages" — mages blink + attack, healers "overcharge" to reset cooldown, repeat many times in one turn. Smite's regrets: never cloned it; a stale-object bug left knights with the pre-move attack range; didn't know the API exposed an enemy factory's current build ("look into the specs a bit more closely ... exploit any (intended or unintended) API features").

#### 2017 — test bot please ignore (Stuart Johnson "stuartj", Milo Hartsoe) — Top 32 of 450+ (self-reported; not a finalist)
- Source: https://web.archive.org/web/2022/https://medium.com/@thestuart/the-lessons-i-learned-from-mits-battlecode-2017-competition-post-mortem-570acdc6c1a2 (live Medium returns 403). Cited by Big Red 2019 as the canonical "working bot first" postmortem.
- Game: 2017 "bullet hell" — archons, gardeners plant bullet trees, soldiers/tanks/scouts/lumberjacks; friendly fire.
- Takeaways (facts from source):
  1. **"Actually make a working bot that wins matches before you do anything else ... sure as hell go and try to conquer the leaderboards in those first few days."** They spent early days on "super well-structured" architecture and had no bot for Sprint.
  2. **Don't over-invest in a unit you should expect to be nerfed** (they perfected scout AI; scouts got nerfed).
  3. **Off-meta surprise works against untested opponents:** a mass-lumberjack "gas cloud" (each stays 2 units from other lumberjacks) beat bots that never saw it — but lost to any soldier micro that simply ran backwards (kiting).
  4. **Troll maps break untested assumptions:** a map where the archon couldn't move froze their bot (build code never tried the free directions) and dropped them to losers' bracket. "We might have even made top 16 had we tried a little bit harder to break our bots. What we didn't know ... the devs are known for making maps to test bots."

---

### Placement verification
Checked against https://battlecode.org/past.html (fetched 2026-09-23): 2021 Baby Ducks 1st, Malott Fat Cats 4th, wololo 7th–8th, California Roll 9th–12th, Three Musketeers 9th–12th, llllllll's Disciple HS 3rd; 2020 Java Best Waifu 1st, Battlegaode 3rd, The High Ground 4th, Bowl of Chowder 5th–6th; 2019 smite 1st, NP-cgw and Big Red "Finalist" (Big Red's "8th" is self-reported); 2018 Orbitary Graph 1st; 2017 Arbitrary Graph Restoration Fund 1st; 2016 future perfect 1st; 2015 the other team 1st; 2014 that one team 1st. Test Bot Please Ignore (2017, top 32) is self-reported only.

### Cross-cutting patterns (synthesis — inference, each tied to sources above)
1. **Copy the leader, then tweak** — stated by 2015/2016 champion McGlynn, The High Ground ("in all our years we have [won with an original strategy] exactly 0 times"), Stone Tao, Big Red, 3 Musketeers. Mechanism: watch replays of the #1 ladder bot, reimplement, add small improvements.
2. **Strategy-independent primitives first** (nav, comms, resource gathering, cheap vision scans) — JBW, smite, High Ground, MFC, Stone Tao, Ivy Zhang. They survive rebalances and meta shifts.
3. **Simple + reliable beats clever + coordinated** — McGlynn's "reliable execution", JBW "every time I'd try to implement a sophisticated strategy that requires a lot of coordination it would always flop", Stone Tao's drone-wall-buster regret, Baby Ducks' idle-units-must-attack fix.
4. **Keep sparring bots, especially rush/imitation bots** — future perfect's `felix15`/`supercowpowers`/`simplesoldier18`; Baby Ducks' rush vs econ pair; High Ground's rush bot; Big Red reproducing Volatile; NP-cgw's turtle/rush/defence triangle; Arbitrary Graph's `./run --tournament` + `./backup`.
5. **Maps decide results; tournament maps are adversarial and differ from practice maps** — Baby Ducks "maps win games", MFC tuned on old maps and lost, wololo/Musketeers blindsided by low-passability ECs, Stone Tao on devs rating maps for rush-friendliness, stuartj's immobile-archon map, future perfect's zombie-heavy Sprint maps.
6. **Comms get attacked** — Prasici replayed recorded messages (2020); 1064CBread signal-spam DoS (2016); NP-cgw poisoned trade channel (2019). Defences: smite's per-map keyed encryption, High Ground changing constants before submission, minimal reliance on messages (Battlegaode).
7. **Compact, deterministic shared context** — smite's cluster indices, JBW's bitmask wall plan, NP-cgw's ID threshold, mod-128 coordinates (MFC/wololo/Stone Tao), wololo's distributed Bellman-Ford gradients, MFC's round-robin fact gossip.
8. **Last-minute untested changes are the #1 self-inflicted loss** — smite's preacher rush, Ivy's turn-900 bug, Stone Tao's drone buster, High Ground's 5 AM rewrite, NP-cgw's church saber. High Ground: pick "one or two impactful improvements ... and make them very well."

### Out of scope / leads
- Newer official postmortems listed on past.html (2022 5 Musketeers; 2023 Gone Fishin', 4 Musketeers, don't @ me; 2024 cout for clout; 2025 Just Woke Up, confused, Om Nom; 2026 Generalized Stroke's Theorem, Lorem Ipsum, alext.app, outercloud.dev) — outside 2014–2021 but likely more relevant to modern engines (PDF copies already sit in /tmp/pm from another agent).
- 2019 Oak's Last Disciple postmortem https://battlecode.org/assets/files/postmortem-2019-oak.pdf and 2020 confused (HS 2nd) https://battlecode.org/assets/files/postmortem-2020-confused.pdf — not read.
- 2019 Double J https://github.com/programjames/BC19Bot/blob/master/Battlecode%202019%20Postmortem/Battlecode%202019%20Postmortem.md — not read.
- XSquare "A Guide to Battlecode" https://battlecode.org/assets/files/battlecode-guide-xsquare.pdf — lead is covering.
- Cory Li's bytecode optimisation post and Steve Arcangeli snippets (referenced by McGlynn's idioms post) — not fetched; URLs UNVERIFIED.
- kcbattlecode 2016 postmortem https://kcbattlecodewordpresscom.wordpress.com/2016/02/02/battlecode-2016-postmortem/ and devyaoyh 2017 https://devyaoyh.github.io/battlecode2017_edoctablet/ — not read.
- Battlecode archive https://github.com/battlecode-archive (Justin Ottesen) — source for old team code.

