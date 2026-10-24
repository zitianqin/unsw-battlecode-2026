# Competitive bot culture: CodinGame snake-likes, CG tooling, Kaggle simulation comps

Research for UNSW Battlecode 2026 (torus snake, up to 64 dragons/team, per-dragon processes, sequential turns, head-on = both die, 500 rounds, C++/WASM, CPU budget per dragon per turn). Written 2026-09-23.
Legend: **[V]** = verified this session from the cited source; **[I]** = my inference; **UNVERIFIED** = could not confirm.

---

## 1. CodinGame snake-like contests

### 1.1 Winter Challenge 2026 "SnakeByte" (sponsor Exotec) — exists, verified

**Basics [V]**
- Ran ~2026-03-10 to ~2026-03-24 (bug thread opened 2026-03-10, post-mortem thread 2026-03-24). https://forum.codingame.com/t/winter-challenge-2026/207974 , https://forum.codingame.com/t/winter-challenge-2026-feedbacks-and-strategies/208044 . Exact start/end timestamps UNVERIFIED (contest page is JS-only).
- 2,382 ranked players; Legend league 291. Languages: Python3 750, C++ 693, Java 215, Rust 163, C# 154. Top 12: 11 C++, 1 C# (eulerscheZahl #10). Source: CG API `Leaderboards/getFilteredChallengeLeaderboard` for `winter-challenge-2026-exotec`.
- Top 12: 1 frictionless, 2 blasterpoard, 3 zlakhdar, 4 bowwowforeach, 5 sZoom, 6 delineate, 7 Sheeesh---, 8 gaha, 9 marwar22, 10 eulerscheZahl, 11 MSz, 12 _H3R0_. (Same API.)
- Referee (Java) published: https://github.com/CodinGame/WinterChallenge2026-Exotec (repo created 2026-01-08). Now a permanent multiplayer game: https://www.codingame.com/multiplayer/bot-programming/winter-challenge-2026-snakebyte

**Rules (from `config/statement_en.html.tpl` in the referee repo) [V]**
- 2 players, side-view grid 15–45 wide x 10–30 high, platforms (`#`) impassable. 1–8 snakebots total on grid (so up to 4 per player). Not a torus.
- Simultaneous moves. Snakes keep moving in last direction (start = UP); command `id UP|DOWN|LEFT|RIGHT`, multiple per line separated by `;`.
- **Gravity**: at least one body part must rest on something solid (platform, power source, other snake) or the snake falls. Falling out of the bottom removes the snake; can extend beyond side/top borders.
- Head moving into platform/body: head destroyed, next part becomes head (if ≥3 parts remain, else removed). Head-on at a power source: each snake eats it. Tail-vacated cells are free (move all, then check) — confirmed by a referee reply in https://forum.codingame.com/t/winter-challenge-2026/207974 .
- Eat power source = grow by 1. End: a player has no snakes, no power sources left, or 200 turns. Win = more total body parts. Tiebreak on length lost exists but was not in the statement (jacek's post, #3 in the PM thread).
- Time: 50 ms/turn, 1000 ms first turn.

**What the top used (PM thread + linked write-ups) [V unless marked]**

| Rank | Player | Search | Eval | Notes / source |
|---|---|---|---|---|
| 1 | frictionless | Smitsimax (one MCTS tree per snake), **nodes merged by (head cell, facing, turn%2)** → tree bounded by 8x grid size; depth 12 | hand-written: win/loss, score, BFS dist to nearest apple (gravity-unaware), elevation, #apples closer to each player (AVX2 Manhattan over ≤64 apples) | Policy-biased selection: illegal moves (wall/body except tail) pruned; prefer apple, supported cell, not drifting to edges. ~50k sim turns/50 ms, only 1/3 of time in sim. PM post #14 in thread 208044 |
| 5 | sZoom | Smitsimax, depth grows with iterations, low exploration C | small value NN | 4–6k iterations/45 ms; ~250k sims/45 ms worst case. Biggest win: fast gravity (`DoFall` skipped via support counts). Turn-1 precomputed gravity-aware BFS per apple per body length ≤13. https://github.com/s-Zoom/winter-challenge-2026-snakebyte-PM/blob/main/README.md |
| 7 | Sheeesh | **PPO**, no search | U-Net (160k params), 17 input planes 40x60 | 760M training steps, "still improving"; ~30 ms inference, timeouts hurt. https://gist.github.com/Sheee-sh/ae2dd0474412d604025fbedf883e89e0 |
| 12 | _H3R0_ | Smitsimax, 45 ms, dynamic depth `playCount<1000 ? 3+playCount/250 : 7+(playCount-1000)/1000` | 10 weighted heuristics, weights tuned with **Optuna** | "Single biggest rank jump" (≈55→top 20) = gravity-aware BFS. Voronoi at every leaf with uint64 bitset rows + AVX2. ~150k sims/45 ms. https://github.com/yassine-nid/Winter-Challenge-2026-Post-Mortem |
| 17 | jacek | Smitsimax depth 8→10, UCB + hand priors (wall −1, apple +1, near-apple +0.25), 2% ε-random | tiny NN from self-play, target ±0.999^roundsLeft, 5 output buckets by map height | Params partly CLOP/Optuna, "ended up mostly hand-picking". Used free LLM chats for perf → 3x more sims; LLMs' "use deque/ring buffer" advice was neutral/worse. ~1500 iterations. Post #3 |
| 28 | Regulus136 | Per-snake sequential beam (w=60, d=20); later snakes use earlier snakes' chosen move | heuristic | Transposition table, altitude bonus, joint pre-pass: no gain. Credits cg-brutaltester. Sorting snakes by length: no measurable benefit. Posts #19, #21 |
| 91 | oidrissi | Beam w=48 d=12, 1-step heuristic opponent | BFS + flood fill | **"Strict testing protocol (20→50→100 games) saved us from chasing noise"**; ~40 failed experiments listed; "opponent model was the ceiling… deeper search with a weak opponent model compounds prediction error". Imitation CNN 72% acc + PPO: 38% vs own beam bot. Post #5 |
| ~150 | luxcem | Beam over own snakes; opponents keep direction | weighted sum | **Cluster snakes by union-find (Manhattan ≤12) and search clusters independently** = big perf win; tiny altitude bonus moved him to Legend. Post #10 |
| 149 | HummusHustler | per-snake BFS → joint beam (worse) → single snakes, pair nearby snakes (≤4) | dist + length | Joint 4-snake beam performed *worse* than independent; pairing nearby snakes was "a massive improvement". Adaptive beam width to hit target depth. Used cgarena for local tests. Post #11 |
| 161 | alexcercos | adversarial hill climbing with rolling windows | "dispute matrix" of who reaches each apple first | Post #9 |
| 164 | 0x6E0FF | per-snake beam w=50 d=20; opponents searched first with 5 ms, then own with 40 ms | dead snakes, size, Manhattan | Local arena = referee + CommandLineInterface.java + **psyleague**; raised referee turn limit to 100 ms and recompiled engine to lift 30 s global limit; "too many similar bots in the pool doesn't reflect real arena, so I cleaned up the pool". Post #7 |
| 231 | Highruler | pure rules: BFS per snake to unreserved food, gravity fix, dead-end avoidance | none | "No A*, no state eval, no ML" still reached Legend. Post #12 |
| mid-gold | Anastaszor (PHP) | priority-list "strategy pattern" | — | Silver: A* to nearest food + avoid void. Gold: Voronoi food split, flood-fill reachability, anti-duplicate/anti-collision among own snakes, stuck detection, attacks. Post #6 |

**Takeaways from SnakeByte [I]**
- Per-agent decoupled search (Smitsimax / per-snake beam) dominated; joint search over all own snakes lost to per-snake + local pairing. Directly relevant to 64 dragons that can't share memory anyway.
- The winning edge was *search-structure* engineering (node merging / transpositions by local state) plus cheap policy priors, not a smarter eval.
- Domain-specific distance (gravity-aware BFS) was the largest single eval gain for two independent players. Battlecode analogue: torus-aware + body-aware distance/Voronoi.
- A pure rule-based bot reached Legend (#231/2382), i.e. top ~10%.
- PPO got #7 but needed 760M steps and days per iteration — not feasible for us.

### 1.2 Other snake/Tron-like CodinGame games

Checked the full list of 97 CG multiplayer games via the CG API (`Puzzle/findAllMinimalProgress` + `Puzzle/findProgressByIds`, 2026-09-23) [V]. Only two are snake/light-cycle games: **Tron Battle** and **SnakeByte - Winter Challenge 2026**. No other CG contest was a snake game. Nearest relatives [I]: *Spring Challenge 2020* (multi-unit Pac-Man on a grid, simultaneous moves, collisions), *Back to the Code* (grid territory painting), *Hypersonic* (grid, Bomberman). Sister file `hungry-geese-and-tron.md` in this folder covers Kaggle Hungry Geese; not repeated here.

**Tron Battle** (https://www.codingame.com/multiplayer/bot-programming/tron-battle, created 2014-03) [V, statement via CG API]
- 30x20 grid, not wrapping, 2–4 players; **players move in turn, not simultaneously** (same as Battlecode). Last one alive wins. 100 ms/turn (first turn 1000 ms per a forum post, #57 in thread below).
- Games are mirrored (each seed played from both sides) to reduce side bias: k4ng0u, post #48, https://forum.codingame.com/t/49/41.json
- Multi-player (3–4) games are ranked as scaled pairwise 1v1 TrueSkill updates, so placement matters, not just winning (RoboStac, post #58; claim unsourced by him — treat as UNVERIFIED).
- What works, from the forum thread https://forum.codingame.com/t/tron-battle-aka-line-racing-multiplayer-challenge-discussion/49 [V]:
  - Gold: Voronoi (cells I reach first) scored as `10000*myCells − 10*oppCells − myLiberties`; low liberties when alone = efficient space filling (skyyker #27).
  - Top 10: add articulation points / "chamber tree" (from a1k0n's Google AI Challenge post-mortem), and run minimax only vs nearest opponent(s) (skyyker #31). "What is really important… is the evaluation function" (Razielwar #28).
  - Pure MCTS with random playouts was weak (stuck in Silver) until playouts were replaced with coverage heuristics and opponent coverage was penalised (Di_Masta #63; EliasHasle #39→#42 scrapped playouts for Voronoi → top 50, later #8).
  - Python can run ~150 Voronoi evals per 100 ms (MathJason #33) — i.e. language speed caps depth.
- **Google AI Challenge 2010 (Tron)** winner a1k0n: https://www.a1k0n.net/2010/03/04/google-ai-postmortem.html [V]
  - Alpha-beta minimax + iterative deepening; "a better evaluation heuristic will always beat deeper minimax searches".
  - Eval = Voronoi territory, fitted as K1·(nodes diff) + K2·(edges diff) with K1≈0.055, K2≈0.194 from **data-mining 11,691 games** — i.e. eval weights regressed from game logs, not guessed.
  - Checkerboard parity bound on fillable space; articulation points (chamber tree); separated-endgame switches to wall-following + exhaustive search.
  - Kept 14 versions for head-to-head testing; "no bot ever totally shut out a previous bot on all maps".

---

## 2. CodinGame bot-dev culture and tooling

### 2.1 Local testing stack [V unless marked]
- **Official referees are Java** (CodinGame game engine SDK, https://github.com/CodinGame/codingame-game-engine). Contest referees are published after/during the contest (e.g. https://github.com/CodinGame/WinterChallenge2026-Exotec). Community pattern: fork the referee, add a `CommandLineInterface.java` runner class, build a jar, then drive it from an arena tool (cg-brutaltester README, below).
- **cg-brutaltester** (dreignier, 228★, still maintained 2026-09): https://github.com/dreignier/cg-brutaltester — `-n` games, `-t` threads, `-s` swap seats (important for asymmetric maps), `-i` initial seed for repeatable runs. Outputs pairwise winrate matrix. README warns: on a 4-core CPU don't use >3 threads (timeouts distort results). C++20 rewrite: https://github.com/Akadine/new-cg-brutaltester .
- **psyleague** (Psyho, `pip install psyleague`): https://github.com/FakePsyho/psyleague — persistent local league: add/remove bots while it runs, TrueSkill ranking (same model as CG, with adjustable `tau`), matchmaking prioritises bots with < `mm_min_matches` games. Bots can emit `[TDATA]/[PDATA] key = value` on stderr to attach per-game metrics to the leaderboard. Warns: "If you spawn too many workers your bots will start timing out randomly." Works with any brutaltester-compatible referee.
- **CGBenchmark** (s-vivien): https://github.com/s-vivien/CGBenchmark — queues batches of games **on CG's own servers** (simulates IDE "PLAY") against chosen real opponents by agentId, fixed seed lists or random seeds; reports winrate + replay links. Used to check self-play conclusions against real opponents (Agade, below).
- **cgarena** (aangairbender, Rust, `cargo install cgarena`): https://github.com/aangairbender/cgarena — local web UI arena; its ADR https://github.com/aangairbender/cgarena/blob/main/docs/adr/0002-replace-continuous-matchmaking-with-candidate-evaluation.md replaced all-vs-all matchmaking with **candidate evaluation**: each new bot is a Candidate played in bounded, staged runs against a pool of Benchmark bots on a deterministic seed sequence plus curated seeds and fresh random seeds; promotion/rejection manual. Ratings: OpenSkill/TrueSkill/Elo/Bradley–Terry. Used by HummusHustler in SnakeByte. [This is essentially the loop we want.]
- **CG-Bundler** (Rust multi-file → single file): https://github.com/MathieuSoysal/CG-Bundler (single-file submission constraint).
- Related SPRT-based harness for Tron: https://github.com/nathanWolo/tron-codingame (found by search; not read — UNVERIFIED details).
- Stats site cgstats (magusgeek) used to look up opponents/agentIds (CGBenchmark README; Tron thread post #31).

### 2.2 Search families used on CG [V]
- **Smitsimax = Decoupled UCT with one separate tree per agent.** Named by MSmits in Code of Kutulu (2018, 4th); each agent descends its own tree by UCB1, the joint action is simulated, each tree backpropagates its own score; you must re-simulate from root each iteration (nodes are information sets, not states). No Nash convergence guarantee (Lisý et al. NeurIPS 2013, https://papers.nips.cc/paper/2013/file/1579779b98ce9edb98dd85606f2c119d-Paper.pdf) but DUCT is empirically "competitive with or outperforms the alternatives" (Tak, Lanctot, Winands 2014, https://dke.maastrichtuniversity.nl/m.winands/documents/wcg13-smmcts.pdf). Tips: C≈0.7 start, depth 10–20 for 50 ms, ~10 random visits before UCB, normalise scores. Team-coordination weakness: allied trees may pick conflicting plans unless eval is team-shared. Source: https://mightyprofessionalgaming.com/tutorials/smitsimax-from-scratch (linked from https://forum.codingame.com/t/smitsimax-tutorial/208247, 2026-05-25). Original CG playground https://www.codingame.com/playgrounds/36476/smitsimax is JS-rendered; not read (UNVERIFIED beyond the tutorial's summary).
- **Beam search**: common mid-Legend approach in SnakeByte (widths 48–60, depths 12–20), usually per-unit sequentially with earlier units' chosen plans fixed for later units (Regulus136, 0x6E0FF). Fails when opponent model is weak (oidrissi).
- **Minimax/alpha-beta + Voronoi eval**: Tron standard (§1.2). Paranoid multi-player variant (all opponents minimise you) helped reach Legend in Tron (dolmen1234 #36).
- **Genetic algorithms / rolling-horizon evolution / hill climbing**: alexcercos' adversarial hill climbing in SnakeByte (#161). [Historically popular in CG racing games (Coders Strike Back, Mean Max) — UNVERIFIED this session.]

### 2.3 How top players iterate (quotes / numbers) [V]

**Workflow & infra**
- reCurse (won Coders of the Caribbean 2017): "I first write throwaway code to get to bronze league using the least effort possible", then a simulator. **Replay downloader → thousands of real replays as simulator unit tests**: "Replaying thousands of matches and validating the expected result turns a simulator cannonproof very quickly, making optimizations and refactors very safe." **State serializer**: dump base64 state to stderr every turn so any bad frame can be reproduced locally. "Profile early, profile often." Lost top-5 form by "making too many changes at once without testing each of them properly". https://recursive.cc/blog/coders-of-the-carribean-post-mortem.html (read via web.archive.org `id_` copy; live site refused connection).
- reCurse, Halite III (3rd): built a "battlestation" first ("Polishing your workflow and iteration time early on gives an incredible payoff"). Key tool: **comparison benchmark = play the same game twice, swapping only my bot version**, so "any change of behavior could be properly measured without the variance noise of different game conditions" and refactors verified by identical game records. CLOP tuning "was not quite as helpful as I hoped" due to noise and "mirror effect in local games". Spawn logic: "a lot of time tinkering… all ultimately defeated by a round of simplification". Aimed for a **stateless bot** (any state in → move out). Architecture: per-unit roles assigned sequentially by priority + a global collision policy. https://recursive.cc/blog/halite-iii-postmortem.html (archived copy).
- reCurse, Wondev Woman (3rd/2300): batch testing "did weed out terrible ideas quickly" but for subtle tweaks "the same run could end with a 70% or 40% win ratio against a good opponent"; "tweaking parameters in a local arena once again rarely found something that translated well in the arena". Ideas that looked good in watched games showed "a noticeable drop in win rate" in batch tests. https://recursive.cc/blog/wondev-woman-post-mortem.html (archived copy).
- a1k0n (Google AI 2010 Tron winner): fitted eval weights by regression over 11,691 games; kept 14 versions for regression testing (§1.2).

**Self-play vs real opponents**
- Agade (2nd, Fall Challenge 2020): used his own local arena heavily; ablation ("amputation") tests quantify each feature, e.g. opponent modelling −30% winrate in self-play, but only ~15% against real players via CGBenchmark: "self-play evaluation is risky when it comes to opponent prediction." https://github.com/Agade09/Agade-Fall2020-Challenge-Postmortem
- 0x6E0FF (SnakeByte): local pool of too-similar bots misleads; prune the pool (§1.1).
- R1FA (Summer 2025, NN #59): pure self-play RL converged to an uninteresting equilibrium; pretraining on real online games "helped a lot"; should have added non-self opponents. https://forum.codingame.com/t/summer-challenge-2025-post-mortem/207074

**Game counts and when to trust results**
- oidrissi (SnakeByte #91): staged 20→50→100 games per change; most of ~40 ideas were "within noise at 50+ games". (§1.1)
- blasterpoard (#2 Summer 2025): a tweak worth "0.07% winrate in 10000 selfplay games"; "once a bot gets good enough, almost nothing matters". Top-of-leaderboard gaps (0.001 TrueSkill) were noise; rerun had too few games. https://forum.codingame.com/t/summer-challenge-2025-post-mortem/207074
- bowwowforeach (#1 Summer 2025): DUCT with no playouts, hand eval with **~36 parameters tuned by Optuna via self-play vs earlier versions**; a last-2-hours fix came from *watching losses* to the #2 bot and adding one targeted exception. Same thread.
- Psyho: "around half of the top20 in the last CG contest used psyleague" (tweet, 2024-06): https://x.com/FakePsyho/status/1800527766292779386 (from search snippet; not opened).
- Statistics [I]: winrate standard error ≈ 0.5/√N → 95% CI ±10 pts at N=100, ±3 pts at N=1000, ±1 pt at N=10,000. So 100-game tests only detect ≥10-pt changes; sub-2-pt tweaks need thousands of games or paired seeds (reCurse's same-seed comparison, brutaltester `-s`/`-i`). SPRT (sequential test, as used in chess engine dev) stops early on clear wins/losses; a Tron SPRT harness exists (https://github.com/nathanWolo/tron-codingame, not read).

**Tuning tools seen**: CLOP (jacek, reCurse — both say noisy/limited), Optuna (bowwowforeach 36 params #1; _H3R0 #12; jacek). Hand-picking still common at the top.

**Recurring post-mortem advice (synthesis) [I, each point backed by a citation above]**
1. Invest days 1–3 in tooling: exact simulator verified against real replays, local arena, state dump/replay-from-state.
2. Change one thing at a time; ablate features to measure them.
3. Use paired seeds / seat swaps; beware self-play for opponent-model changes; keep a diverse opponent pool including real opponents' replays.
4. Watch losses to the current top bots and patch targeted weaknesses late.
5. Simplify: several top players report simpler rules beating tuned complexity.
6. Speed first: every SnakeByte top write-up spent most time on simulation speed (50k–250k sims/turn).

---

## 3. Kaggle / Two Sigma simulation competitions

### 3.1 Halite I–IV

| Season | Dates / length | Winner | Approach | Source |
|---|---|---|---|---|
| Halite I (halite.io) | closed 2017-02-16; ~3 months [length I] | mzotkiew (2 shummie, 3 erdman); 1500+ competitors | rule-based [I from top-10 write-ups; not verified for mzotkiew himself] | https://ceklopfenstein.github.io/Halite-1/ (archived site copy) |
| Halite II | ended 2018-01-29 | reCurs3 (reCurse), 2 FakePsyho (Psyho), 3 shummie | rule-based + a "tactical pass": random per-ship action perturbation (hill climbing) scored by simulating against 19 precomputed enemy responses | https://www.twosigma.com/articles/halite-ii-concludes-winners-announced/ , https://recursive.cc/blog/halite-ii-post-mortem.html |
| Halite III | 2018–19 [dates not verified] | teccles (2 SiestaGuru, 3 reCurse) | rule-based: per-ship Dijkstra "turns-to-fill" score per square, then assignment; "I tried adding in plans about what square would be mined after the first one. These never helped" | https://github.com/teccles-halite/halite3-bot , https://recursive.cc/blog/halite-iii-postmortem.html |
| Halite IV (Kaggle "Halite by Two Sigma") | Jul–Sep 2020, ~3 months; 1,143 teams | Tom Van de Wiele (tvdwiele) | **rule-based, 11,000+ lines** after abandoning deep RL | https://www.kaggle.com/c/halite/discussion/183543 , https://github.com/ttvand/Halite |

**Halite IV winner detail** (Kaggle writeup, fetched via Kaggle's JSON API) [V]
- Ex-DeepMind; had used deep RL to briefly top ConnectX. Spent **a month on deep RL** "trying to get the ships to do anything different from standing still"; cause: credit assignment "with an arbitrary number of units, a long episode duration and a dynamic opponent pool", plus compute.
- Architecture: (1) per-ship score maps over all 21x21 cells for each goal (collect, return, build base, attack base), with an exponentially decaying distance mask so the argmax is **stable between turns**; (2) planner assigns one task per ship, resolving conflicts by priority; (3) translate plan to moves with collision handling. "Allows for smart overrides at all parts of the pipeline."
- Heavy **opponent modelling from observed behaviour**: per-opponent risk profile from the last 120 "risky" moves; hierarchy of 1-step/2-step/N-step "bad actions" masked out; chase and cycle detection.
- **Copied strategies from stronger live-LB opponents** (mzotkiew's hunting destroyed him mid-comp; "I gave up on the peaceful mining approach and decided to imitate and try to improve on his strategy").
- 22 agents submitted, 16 would have taken 1st.

**Halite IV 8th (gold): imitation learning from leaderboard replays** — Kha Vo, Dan Grunberg, Gilles Vandewiele, Rob Gardiner, Tung Phung. https://www.kaggle.com/c/halite/discussion/183312 , code https://github.com/digitalspecialists/halite4 [V]
- Scraped **>100,000 matches**; most submissions trained on ~**3,000 top matches, refreshed daily**. Per-turn board → U-Net (EfficientNet encoder) → per-cell action probabilities → linear-sum assignment so friendly ships never collide. Toroidal random-crop augmentation; enemy-channel swap augmentation.
- **Pure IL bot ≈ rank 20**; hand heuristics overriding spawn/convert/base protection lifted it to 8th. Agents imitating 1330+-rated games reached 1400+ once heuristics were added (paraphrase; original has a typo). IL reproduced the *average* behaviour of imitated teams, but could not match top manual attack strategies.
- Only needed a few thousand top games; but required GPU training and a team of ML practitioners over a 3-month comp.

**Takeaways [I]**: In all four Halite seasons the winner was hand-written. ML's best showing was a hybrid (IL core + rule overrides) at 8th. Common winning structure = score/utility per unit per target → global assignment with collision resolution → stable targets across turns → opponent-specific tweaks. reCurse Halite II: "Local testing against previous versions was helpful in the beginning, but… increasingly inaccurate and pointless over time. I often had versions performing much better online while still performing poorly against the previous version."

### 3.2 Lux AI Seasons 1–3

| Season | Dates / length | Winner | Approach | Source |
|---|---|---|---|---|
| S1 (lux-ai-2021) | Aug–Dec 2021, ~4 months [dates I]; 1,178 teams | **Toad Brigade** (Isaiah Pressman, Liam, Rob) | **deep RL from scratch** (IMPALA + UPGO + TD-λ, frozen-teacher KL term, reward shaping for first 20M steps then sparse win/loss; 8→16→24-block nets distilled from previous); single home PC, 8-core + 2 GPUs, "training overnight most nights" | https://github.com/IsaiahPressman/Kaggle_Lux_AI_2021 (copy of https://www.kaggle.com/c/lux-ai-2021/discussion/294993) |
| S2 (lux-ai-season-2) | early 2023 [dates UNVERIFIED]; 646 teams | **ry_andy_** (Ryan Dy) | **rule-based** Python | https://github.com/ryandy/Lux-S2-public |
| S3 (NeurIPS 2024) | Dec 2024 – 10 Mar 2025 (last submissions) | **Flat Neurons** (avolchek, tonyk98, ekaterinayurkovskaya, ixanezis); 2 Frog Parade; 3 aDg4b | Flat Neurons approach **UNVERIFIED** (Kaggle writeup JS-only). 2nd Frog Parade = **deep RL** (PPO + teacher-KL, Rust sim+features, 10M-param CNN, ~300M game steps ≈ 8 days on 2 GPUs). 3rd (andreyd41) = **imitation learning** on Frog Parade + Flat Neurons replays | standings https://clist.by/standings/neurips-2024-lux-ai-season-3-games-artificial-intelligence-reinforcement-learning-custom-metric-56115841/ ; https://github.com/IsaiahPressman/kaggle-lux-2024/blob/main/write-up.md ; https://github.com/andreyd41/lux3-bot |

**Key facts [V]**
- Toad Brigade started with parallel rules + RL tracks, expecting rules to win "motivated by the top results in last year's Halite"; "within the first month the RL approach began to beat the rules-based one… so the rules-based agent was abandoned". Game phase feature (turn/40) was "a crucial part of its success". A few hand rules layered on at test time (city build ordering).
- Frog Parade (Lux S3 2nd): sim correctness via **integration tests comparing own sim with the official engine across many seeds** — "I spent no time debugging the simulation once the tests were passing" and found bugs in the official engine; tests caught a mid-comp rules change. 110k env steps/s on 16 cores. ~10.8k lines Rust + 6.5k Python.
- **Lux S1 imitation learning**: 9th/1178 = hybrid rule engine + CNN imitation model (https://github.com/vitoque-git/Kaggle-luxai-Season1-Python-Framework); 19th = imitation agents trained on top-player episodes downloaded via the Kaggle API (https://github.com/shmyak-ai/lux-ai).
- **Lux S3 imitation (3rd)**: trained only on replays of the top two teams; kept only matches the imitated agent won; dropped matches after the game was decided; re-ran own feature code on the replayed agent's observations; mirrored maps to a canonical spawn. https://github.com/andreyd41/lux3-bot

### 3.3 Kore 2022 (Kaggle, ~Apr–Jul 2022 [dates I]; ~500 teams per 5th-place repo)
- **Winner Harm Buisman: rule-based**, forked from the Kore **Beta** 1st-place rule-based agent by egrehbbt (+5,923/−777 lines). Worked "like one would develop a software product": issue list, version control, fix a few issues per iteration, a **replay visualiser to find why each episode was lost**. 146 versions; V92 held #1 for weeks; "It took me two weeks and 26 updates to get agent V118 that was consistently better in 1v1 matches." Final changes were only parameter tweaks. A late opponent found a shared weakness (paralysing his first shipyard) that "collected points from all my agents". https://www.kaggle.com/competitions/kore-2022/discussion/340035 [V via Kaggle JSON API]
- 4th (qihuaz): "started off with Reinforcement Learning but soon switched to rule-based… convinced that RL alone cannot win this competition"; "Defense is more effective than offence". https://www.kaggle.com/c/kore-2022/discussion/340157 [V]
- 5th (itswin, 1Musketeer), rule-based code public: https://github.com/itswin/Kore-2022 [repo description only].
- Imitation: khanhvu207 trained a Transformer on ~200M (observation, ship-plan) tuples from the top-5 submissions (https://github.com/khanhvu207/kore2022). Final rank UNVERIFIED.

### 3.4 ConnectX and others
- ConnectX (Kaggle, ongoing playground, Connect-4): Tom Van de Wiele (later Halite IV winner) used deep Q-learning to "briefly" reach #1 (his words in the Halite IV writeup; DQN Colab https://www.kaggle.com/c/connectx/discussion/129145). Connect-4 is a solved game, so strong agents can use exact search/opening books [I; not checked on the leaderboard this session]. Low relevance.
- Kaggle Hungry Geese (snake, 2021): see sibling file `hungry-geese-and-tron.md`.
- Kaggle Rock-Paper-Scissors 2020 / Santa: not researched (low relevance; Santa comps are optimisation puzzles, not bot arenas).

### 3.5 Pattern across Kaggle/Two Sigma comps [I, from the rows above]
- **Rule-based winners**: Halite I, II, III, IV, Lux S2, Kore 2022. **RL winners**: Lux S1 (months of nightly training). S3: RL/IL teams in top 3; winner's method unverified.
- RL won when the game rewarded long-horizon positional play and the team had months + GPUs; it lost where many units need tight coordination, episodes are long, and credit assignment is hard (Halite IV winner's explicit reason for quitting RL).
- **Imitation learning from public replays** was reliably *good but not winning*: ~rank 20 pure, 8th with rule overrides (Halite IV); 9th/19th (Lux S1); 3rd (Lux S3, imitating the top 2). IL copies the average of whom it imitates; hand rules on top are what push higher.
- **Time horizon**: these comps ran ~3–4 months with daily submissions. Kore winner needed ~2 weeks and 26 versions for one decisive improvement; Lux S1 RL needed "the first month" just to overtake rules. We have ~3.5 weeks to the last tournament and ~8 days to the first (1 Oct 2026) — shorter than any of these, and matching CG contests (~1–2 weeks) instead, where search + hand eval wins.
- **Copying the ladder**: every winner with public replays mined them — Halite IV winner copied mzotkiew's hunting; Kore winner forked the Beta winner; IL teams trained on top replays.

---

## 4. Transferable to Battlecode (3.5 weeks, no model training)

1. **Build tooling before strategy (days 1–3).** Exact simulator checked against the official engine over many seeds (Frog Parade), real replays turned into regression tests (reCurse CotC), state dump → replay a single turn locally (reCurse). Every CG winner spent most of their time on sim correctness and speed (SnakeByte: 50k–250k sim turns per 50 ms).
2. **Candidate-vs-pool gate, not free-for-all.** The loop should mirror cgarena's ADR: each new bot plays a bounded, staged run against a fixed pool of earlier bots on a fixed seed list plus fresh seeds, with seat swaps. Promote only on a clear win. Use **paired seeds** (same map, swap only our version; reCurse's "comparison benchmark") to cut noise.
3. **Size tests to the effect.** ±10 pts at 100 games, ±3 at 1,000 [I, binomial]. Stage it like oidrissi (20→50→100+) or SPRT: kill clear losers early, spend games on close calls. Expect most ideas to land "within noise" (oidrissi: ~40 failed; blasterpoard: +0.07% over 10k games). Our 0.5–10 s matches make ~1,000 games per candidate practical if parallel, within the CPU-load limits.
4. **Don't trust self-play alone.** It overstates opponent-model gains ~2x (Agade: 30% in self-play vs 15% against real bots). Local tests stop predicting the ladder over time (reCurse Halite II, Wondev Woman), and a pool of near-identical bots misleads (0x6E0FF). Keep the pool diverse, and **use public ladder replays**: re-implement the top teams' visible behaviours as pool opponents, then check against real ladder Elo.
5. **Mine public replays for strategies, not weights.** Every Kaggle winner copied what worked on the ladder (Halite IV winner imitated mzotkiew's hunting; Kore winner forked the Beta winner). IL got top-10 to top-20, but needed GPUs and ~3 months. With no training, the equivalent is: watch our losses to top teams each day and hand-code counters (bowwowforeach's last-2-hours fix won Summer 2025).
6. **Per-dragon decoupled decisions plus cheap local coordination.** Joint search over all own units lost to per-unit search in SnakeByte (HummusHustler, luxcem). Winners used per-snake trees (Smitsimax) or per-snake beams, where later units see earlier units' chosen moves (Regulus136). That fits our no-shared-memory, sequential-by-id turns directly: dragon k can take dragons <k's moves this round as fixed. Pair or cluster only nearby dragons (union-find at distance ≤ ~12, luxcem).
7. **Eval: domain-correct distance + Voronoi > deeper search.** The largest single gains were gravity-aware BFS (_H3R0: ~55→top 20) and Voronoi / articulation points in Tron (a1k0n: "a better evaluation heuristic will always beat deeper minimax searches"). For us: torus-aware, body-aware BFS/Voronoi (a tail cell frees up in N turns), plus a flood-fill trap check.
8. **Hard safety layer before scoring.** Mask moves by badness level: 1-step (can die this turn, including an enemy head-on — both die), 2-step (no escape next turn), N-step (Halite IV winner). Frictionless and jacek pruned wall/body moves in selection. Weigh a head-on trade by material, not always avoid it [I].
9. **Stable per-unit targets via deterministic assignment.** Halite IV/III winners: score every unit×target, assign greedily with conflicts resolved by priority, and use distance-decayed scores so targets don't flip between turns. With no shared memory, each dragon can recompute the *same* team assignment from the same visible state, giving implicit coordination [I]. Stateless design (reCurse) simplifies the per-process setup.
10. **Tune few parameters, late, and in bulk.** Optuna/CLOP helped when the eval had many weights (bowwowforeach 36 params, #1). But many report noisy, hard-to-transfer gains (reCurse, jacek hand-picked). Simplification often beat tinkering (reCurse Halite III spawning). Change one thing per candidate (reCurse CotC's "too many changes at once").

---

## Out of scope / leads (not researched here; would be filed as issues)
- Kaggle Hungry Geese (snake): covered in `hungry-geese-and-tron.md`; cross-check that file's claims against §1.1/§4 here.
- Flat Neurons' Lux S3 1st-place method: Kaggle writeup https://www.kaggle.com/competitions/lux-ai-season-3/writeups/flat-neurons-1st-place-approach-by-flat-neurons is JS-rendered; the Kaggle JSON API (`discussions.DiscussionsService/GetForumTopicById`) works if the topic id is found.
- Battlesnake (play.battlesnake.com) and the Google AI Challenge Tron field beyond a1k0n: highly relevant snake-arena literature, not covered.
- SPRT harness for Tron (https://github.com/nathanWolo/tron-codingame): possible template for our promotion gate.
- SnakeByte #2 blasterpoard and #3 zlakhdar posted no write-ups in the thread (checked all 21 posts). Their replays are on CG and worth watching for multi-snake tactics.
- LLM-assisted perf work: jacek got 3x sims from free-LLM suggestions, but LLM "use deque" advice was neutral; relevant to our Claude loop.
- The Smitsimax playground (https://www.codingame.com/playgrounds/36476/smitsimax) and psyleague source (TrueSkill `tau` choice) could be read for exact defaults.
