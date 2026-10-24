# Battlesnake: top bots, algorithms, evaluation terms

Battlesnake = multiplayer snake over HTTP; each snake is a web server that gets the board state each turn and must answer within a timeout (historically 200 ms in 2018, 500 ms in standard). Snakes lose 1 health/turn, food restores to 100 and grows by 1. Head-on: the **longer** snake survives, equal lengths both die. (Differs from Battlecode, where head-on kills both regardless of length.)
Rules: https://docs.battlesnake.com/rules

Legend: **[V]** = verified by reading the source this session; **[I]** = my inference; **UNVERIFIED** = could not confirm.

---

## 1. Graeme Hill "snakebot" (1st, Victoria Battlesnake 2018)
Source: http://web.archive.org/web/20240224101502id_/http://graemehill.ca/battle-snake/ ; code https://github.com/graeme-hill/snakebot  [V]

- A* pathfinding that knows when tail segments will have moved out of the way (a cell occupied by a body segment k from the tail is free in k turns).
- Base "algorithms" (policies): **Hungry** (go to nearest food), **Chase tail** (loop on own tail, starves), **Defensive hungry** (chase tail unless there's food you can reach before any enemy). Rationale: no point going for food someone else reaches first.
- **Policy rollouts instead of full search**: simulate futures for every combination of (my policy × enemy policy). All enemies share one policy per future so branching doesn't grow with enemy count ("a potential flaw").
- **Prefix moves**: each policy is run after each of the 4 possible first moves → 4× more diverse futures. "Surprising improvement ... the snake suddenly starts to seem like it is navigating on its own." Branches 4 → 64.
- Ported JS → C++ for speed (memory pools, no heap allocs in hot path, fewer A* calls, inlining), then parallelised branches across cores; ran on a 72-core EC2 box for the event.
- Aggression: added head-chasing / cut-off policies both for us and as enemy models; scoring prefers futures where enemies die.
- Three failure causes they list: (1) scoring function wrong, (2) only simulated fatal futures when a safe one existed, (3) horizon too short (a 10-long tunnel with 9-step lookahead). Fix: heuristic "moving into a box smaller than your body is bad".
- Final lesson: most value came from playing many games vs other snakes and fixing recurring loss patterns (suicide tunnels; corner-adjacent to a bigger snake). Last-day fixes shipped untested.

**Transfer [I]:** "policy × prefix move" rollouts are cheap, need no evaluation function beyond outcome + length, and are a good first lookahead for a per-dragon bot under a CPU budget. Avoid the "box smaller than body" trap explicitly with a flood fill.

## 2. Asymptotic Labs (PPO RL + alpha-beta override; top-8 SHAC 2020, later won Communitech veteran)
Source: http://web.archive.org/web/20250419085627id_/https://medium.com/asymptoticlabs/battlesnake-post-mortem-a5917f9a3428 [V]

- Had previously done minimax/alpha-beta; "after spending far too many hours tweaking our hand-coded heuristic" switched to PPO self-play.
- Hybrid: RL policy picks move; **alpha-beta overrides only if it proves the policy move is a forced loss or finds a forced win**. "Helps slightly at the higher end of competition where tree search snakes dominate."
- Reward: +1 win, −1 loss/tie, 0 otherwise (no shaping, to avoid human bias).
- 17-channel 23×23 egocentric input (head centred), 4-layer CNN. ~524M game turns in 4 days on one desktop GPU.
- **Gating new versions**: every 50 iterations, 3000 4-player games vs 3 prior bests; promote only if win rate > 30% (25% = parity). Opponent mix 80% current best / 20% older bests to avoid rock-paper-scissors "strategy collapse" (non-transitive cycling).
- "PPO snake has yet to dominate our Alpha-Beta strategy" but runs in <15 ms vs 500 ms.

**Transfer [I]:** the gating rule (promote only after a large match vs *several* prior versions, not just the latest) and the older-opponent mix are directly usable in our improvement loop even without ML. The "search as a safety override on top of a cheap policy" pattern fits our CPU budget.

## 3. Shapeshifter (Jonathan Arns) — "most successful battlesnake in the world in 2022"
README claim: two 1st places + one top 8 at the four 2022 elite tournaments. https://github.com/JonathanArns/shapeshifter (Rust, ~4.4k lines; read from source this session, last commit 2026-05-04) [V]

Search (`src/minimax/mod.rs`) [V]:
- **Paranoid 2-level search**: my move (max node) then **all enemies as one combined min player** (`ab_min`). Simultaneous moves are modelled as "I move first, enemies reply knowing my move" → pessimistic.
- **Enemy move-combination pruning**: `ordered_limited_move_combinations` does not enumerate the Cartesian product of enemy moves. It builds only enough joint moves that *each enemy's each legal move appears at least once* (≤4 combos regardless of enemy count), ordered by history heuristic. This is the key trick that keeps multi-snake search tractable.
- Iterative deepening with **Best Node Search / MTD(f)-style null-window probes** (`next_bns_guess`), root moves shuffled, deadline check → return previous depth's answer on timeout.
- **Transposition table** (Zobrist-style hash, stores depth, bound type, best moves), **history heuristic** move ordering (`history[head_cell][dir] += depth`), TT move tried first.
- **Search extension** when the position is forcing (my_moves × enemy_moves ≤ 1).
- **Quiescence search**: at depth 1, if any enemy head is within distance < 3 of mine the position is "unstable" and search continues up to 5 more plies (standard mode). I.e. never evaluate mid-head-on-fight.
- **MCTS fallback**: if minimax says a loss is near-certain (score close to MIN), use UCT (`src/uct.rs`) instead — paranoid search gives up in lost positions; MCTS finds moves that win if the opponent errs.
- Terminal scoring: loss = MIN + turn (prefer dying later), win = MAX − turn (prefer winning sooner), draw = −10000 + turn.

Evaluation (`src/minimax/eval.rs`) [V] — linear, **tapered** (separate early/late weights interpolated by turn progress, like chess mid/endgame eval). Standard-mode terms (early, late weight):
- `my_health` (1,0), `lowest_enemy_health` (−2,0)
- `being_longer` (9,0): signed log₁.₅(length diff+1) × board width — diminishing returns for extra length
- `controlled_food_diff` (0,3): food in my Voronoi region minus enemy's
- `area diff` (1,7) using **checkerboard-parity-corrected** area (`checkered_area_size`, same parity trick as a1k0n's Tron bot: a path alternates colours so usable area ≈ 2·min(black,white)+1)
- `distance to closest food` (7,0)
- `controlled_tail_diff` (6,20): whose Voronoi region contains snake tails (tails are escape routes)
- Wrapped modes add `length % 2` (odd length) and hazard terms.
- **Area control** = simultaneous multi-source BFS on bitboards (Voronoi): my head vs all enemy heads, contested cells go to nobody; in duels the **longer snake wins contested cells** (because it wins head-ons). Wrap handled with edge-mask shifts.
- **Endgame solver** (`endgame.rs`, duels, turn ≥ 50): if regions are separated, decide the winner by comparing region sizes (+ food access) and return a near-terminal score.

Tuning (`src/bin/trainer.rs`) [V]: **genetic algorithm** over 26 eval weights, population 200, 20 games per generation, 2 snakes per game, tournament selection size 2, 10 mutations/gen, each weight with a hand-set range. (Note: 20 games per individual per generation is a very noisy fitness signal; see testing-methodology.md.)

**Transfer [I]:** (a) Joint-enemy "each move appears once" combos + paranoid min is the most practical way to search with many units. (b) Quiescence around nearby heads maps directly to our head-on-kills-both rule. (c) Tapered eval + parity-corrected Voronoi + tail/escape control are cheap, well-tested terms. (d) MCTS/"hope" fallback when paranoid search says lost.

## 4. coreyja (battlesnake-rs; Elite Winter Classic 2021 invitee)
Sources: https://coreyja.com/posts/BattlesnakeMinimax/Minimax%20in%20Battlesnake/ ; https://github.com/coreyja/battlesnake-rs [V]
- Depth-limited minimax; one "depth" = one snake's move, so one game turn = N plies with N snakes.
- **Paranoid** (all opponents minimise my score) vs **MaxN** (each snake maximises its own score component). Paranoid allows alpha-beta; MaxN cannot prune and needs N evaluations per leaf. Author: MaxN "feels" like a better model, paranoid is faster — no empirical verdict given.
- Eval of "Hovering Hobbs": flood-fill area control. "Devious Devin": distance to food / closest opponent head. Also an MCTS snake ("Improbable Irene"). No published win-rate comparison — UNVERIFIED which was stronger.
- Branching estimate: 3 non-suicidal moves per snake → 9 joint moves/turn in duels → 9⁵ ≈ 59k nodes for 5 turns.

## 5. Team "Niedersächsische Kreuzotter" (2nd Intermediate, Battlesnake Victoria 2019) + paper
Sources: https://github.com/m-schier/battlesnake-2019 ; paper "Adversarial N-player Search using Locality for the Game of Battlesnake" https://dl.gi.de/handle/20.500.12116/29001 [README V; paper see below]
- Iterative deepening to a time cutoff. **Locality**: only fully simulate opponents whose body is reachable from my head within the search depth; others get a "reflex" policy (just any non-suicidal move) at the leaves.
- If only 2 snakes are fully simulated → alpha-beta; if more → **MaxN** (no pruning, much shallower).
- Noted the pessimism of sequentialising simultaneous moves ("the opponent may counter our move perfectly ... not possible in reality").
- Very good at not dying (won the Dyspatch bounty where snakes gang up on you), but "our heuristics were sub par ... idling too much" → length disadvantage lost the final. Lesson: survival search without a length/territory incentive loses on tiebreak.

## 6. IEEE CoG Snakes AI Competition 2020/2021 (Innopolis) — 2-player simultaneous snake
Source: https://arxiv.org/pdf/2108.05136 [V]
- Rules: 2 snakes, simultaneous moves, one apple at a time, head-on → longer wins, equal = draw; 1 s/move; 3-minute games.
- 2020 winner Eita Aoki (Heroz); 2021 winner Team Serpentine (Eindhoven), 192 wins vs 152 for 2020 champ. Top bots: **iterative deepening, alpha-beta, MCTS, A***; hand-designed evals.
- Serpentine eval = length + distance to apple + **Voronoi count of empty cells closer to me than the opponent**.
- Notable exploit: when ahead on apples, Serpentine **circled the apple to deny it** and won on timeout — organisers added an apple-respawn rule in 2021. **[I] Our analogue: once ahead on the tiebreak (longest dragon / total length), switch to denial + safety; the game only needs to be won at round 500.**
- Top players "prioritized survival over collecting apples" — as snakes grow, collision risk grows but apple supply doesn't.
- Organisers ran 3 games per pair (2020), 30 per pair (2021) "to mitigate randomness" — they acknowledge 3 was too few.

## 7. Research papers (brief)
- **Albatross** (Mahlau, Schubert, Rosenhahn 2024) https://arxiv.org/abs/2402.03136 [V abstract]: AlphaZero variant for *simultaneous* games; plain AlphaZero fails to exploit weak agents in Battlesnake because simultaneous moves need a model of the opponent's (bounded-rational) play. Relevance [I]: low for us (no training), but confirms that in simultaneous games "assume a perfect opponent" (paranoid) under-exploits weak opponents. **Our game is sequential**, which removes most of this problem.
- **Battlesnake Challenge** (2020) https://arxiv.org/pdf/2007.10504 : MARL playground with human-in-the-loop ("heuristics" injected into RL). [V title only; not read in full]
- **Adversarial N-player search using locality** https://dl.gi.de/handle/20.500.12116/29001 — see §5 (paper PDF not read; README summary only).

## 8. Other top-tier context
- 2022 Spring League top 3 (Battlesnake blog, 2022-03-08): **Jaguar Meets Snake, Combat Reptile, Shapeshifter**, "putting even more distance between themselves and the remaining Elite Tier". https://docs.battlesnake.com/blog/2022/03/08/spring-league-w3-update [V]. No public write-up found for Jaguar Meets Snake or Combat Reptile (searched) — their methods are UNVERIFIED.
- Same page: the 17th/21st/23rd place snakes had ~28% win rate over ~2,050–2,100 games each in 4-player games (25% = parity) — i.e. mid-elite snakes are only a few % above parity; tiny edges are what separate ranks [V numbers, I interpretation].
- 2023 RBC Battlesnake: 2nd place "Orion's Fang" = paranoid minimax + alpha-beta to depth 7, flood-fill + edge-control eval (search-result snippet, repo https://github.com/ycheng11065/2023-python-snake — README not read, UNVERIFIED detail). Winter 2022 RBC winner: minimax + alpha-beta, stateless https://github.com/calvinl4/battlesnake-winter-2022 (title only).
- Nettogrof's "journey to elite" blog https://nettogrof.github.io/ [V]: top snake devs use automated scenario **unit tests** ("we don't want to deploy a new snake that do stupid mistake once live"); tree search + eval is the step to elite; "you will need a lot of testing" to decide eval-vs-speed trade-offs.

---

## 9. Synthesis: core algorithms as used by top snakes (with Battlecode mapping)

| Technique | What it is | Who used it (evidence) | Battlecode mapping [I] |
|---|---|---|---|
| Flood fill / reachable area | BFS from a candidate cell, count cells reachable before being blocked | Everyone; official guide https://docs.battlesnake.com/guides/useful-algorithms | Core safety filter per dragon. Make it **time-aware**: a segment k from the tail frees after k tail advances (Graeme Hill) — but here the tail cell is still blocked on the turn it would move, and growth (eating) delays freeing. On torus, wrap indices. 32×32 flood fill = 0.3M of 100M CPU pts in C++, so ~hundreds per turn are affordable. |
| Parity-corrected area | Usable path length in a region ≈ 2·min(black,white)+[imbalance>0] on a checkerboard colouring | Shapeshifter `checkered_area_size`; originates in a1k0n's Tron bot | Only matters in tight endgame spaces; cheap. |
| Voronoi / territory | Multi-source BFS from all heads; cell belongs to whoever reaches first; ties to nobody (or to the longer snake when longer wins head-ons) | Shapeshifter, Serpentine, Hovering Hobbs, battlesnake_crystal (credits a1k0n) | With 30–40 dragons per side, do one multi-source BFS per team (heads of all friendly vs all enemy dragons). Ties → nobody, since head-on kills both regardless of length. |
| Head-to-head rules | Longer snake wins head-on in Battlesnake → longer snakes seek head-ons, shorter avoid | Shapeshifter area_control tie rule; quiescence near heads | **Different in our game: head-on kills both.** So a head-on is a 1-for-1 trade; it is good for us only when our dragon is worth less than theirs (shorter/cheaper), or when numbers favour us. See swarm-coordination.md. |
| Food/length strategy | Tapered weights: food distance & length early, area & tails late; log-diminishing value of length lead | Shapeshifter eval; "Defensive hungry" (Hill): only chase food you reach first | Pearls were predictable from a fixed seed until 2026-09-25; each match now draws a random seed, so only seen countdowns can be planned around. Tiebreak is *longest dragon* then total length, so late game should consolidate length into one safe dragon. |
| Tail chasing | Follow own tail: always-safe cycle | Hill ("chase tail"), coreyja Eremetic Eric | **Rule difference:** in Battlecode moving into your own tail kills you even though it is about to move (`docs/unsw/death.md`, `execution-order.md`) [V]; you may only enter the cell the tail vacated on an earlier turn. A loop still works if the head trails the tail by ≥1 empty cell. Holding pattern once ahead; an enemy can still kill it by entering the gap. |
| Minimax + alpha-beta (paranoid) | Treat all opponents as one minimiser | Shapeshifter, Devious Devin, RBC winners | Our turns are sequential by dragon id, so a real alternating game tree exists — no need to pretend simultaneous moves are sequential. |
| MaxN | Each player maximises own score; no pruning | m-schier (>2 snakes), coreyja | Not worth it: our opponent is one team; paranoid fits 2-team zero-sum. |
| Locality | Only search opponents that can interact within horizon; others get reflex policy | m-schier/Kreuzotter | Essential with 64 dragons: search only enemy dragons within ~d cells. |
| Limited joint enemy moves | Only generate joint moves so each enemy's each move appears once | Shapeshifter | Same idea for 3–6 nearby enemy dragons. |
| MCTS / UCT | Sampling-based tree search | Shapeshifter fallback, Irene, Snakes AI 2021 top-3, Hungry Geese (see other file) | Fallback when paranoid says lost; or main search if eval is weak. |
| Iterative deepening + time budget | Search depth 1,2,3… until deadline; keep last complete answer | Shapeshifter, m-schier, Snakes AI 2020 top-2 | Budget is CPU points not ms: measure points per node and stop at a fixed fraction of 100M. |
| Move ordering | TT move first, history heuristic | Shapeshifter | Standard; cheap wins. |
| Transposition table | Hash positions to reuse results | Shapeshifter | Useful inside one dragon's search; no cross-dragon sharing possible. |
| Quiescence / extensions | Don't evaluate when heads are within 2; extend forced lines | Shapeshifter | Directly maps: never evaluate a leaf with enemy head adjacent-ish. |
| Policy rollouts × prefix moves | Simulate a few scripted policies after each first move | Graeme Hill (won 2018) | Cheapest robust lookahead; a good v1. |
| Search as veto over a policy | Cheap policy picks, search only overrides proven losses/wins | Asymptotic Labs | Good architecture for a rule-based bot + safety search. |

**Evaluation terms seen in top evals** (Shapeshifter unless noted): win/loss with turn-distance adjustment; own health; lowest enemy health; length diff (log-scaled, capped); food in my Voronoi region; parity-corrected territory diff; distance to nearest food; tails in my region; odd length (wrapped maps); hazard area; "box smaller than body" penalty (Hill); edge control (RBC 2023, UNVERIFIED detail).

**Heuristic tuning seen:** hand-tuning (most); GA with 20 games/individual/gen (Shapeshifter trainer); Optuna/CLOP in CodinGame (see competitive-bot-culture.md). No Battlesnake source found using SPRT or proper significance testing — UNVERIFIED either way.

## 10. Battlesnake → Battlecode: what does not transfer
- Battlesnake is **simultaneous**; Battlecode turns are **sequential by dragon id** (brief, verified by main agent). Much Battlesnake machinery (paranoid "I move first" pessimism, Albatross, decoupled bandits) exists to cope with simultaneity. With sequential turns we can compute exact replies for dragons that move after us in the same round, and know for certain where dragons that moved before us now are.
- Battlesnake: 1 snake per player, up to ~8 snakes. Battlecode: up to 64 per team, one process each, no shared memory. Search must be local and each dragon must predict its teammates' moves (they run the same code → can simulate them deterministically; see swarm-coordination.md).
- Battlesnake has health/starvation; Battlecode has none (rules digest in `research/unsw-game-analysis.md` lists no health rule) but has split, sprint, a 7×7 vision limit, kelp/portals and forgeable sonar.
- Head-on: longer-wins vs both-die changes the whole aggression calculus.
