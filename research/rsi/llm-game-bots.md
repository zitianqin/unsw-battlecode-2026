# LLMs writing and improving game-playing code — research notes

Scope: LLMs / LLM agents that write or iterate on game-playing *programs* (code policies, heuristics), plus what top human Battlecode teams actually did for testing/tuning. Written 2026-09-23 for the UNSW Battlecode 2026 self-improvement loop.

Legend: **VERIFIED** = I read the primary source this session. **INFERENCE** = my reasoning. **UNVERIFIED** = could not confirm from a primary source.

## TL;DR (for designing our loop)
1. **Closest precedent: FAMOU** (arXiv 2606.10389). An LLM evolved 500–1,700-line Python strategies for a 3v3 adversarial CTF game over 400 iterations and took 1st in the AAMAS 2026 hardware round. What made it work was the evaluator, not the seed: champions join the opponent pool, the hardest opponent is up-weighted, and candidates are screened with few games then confirmed with 20 per opponent, because **3-game scores were noise (ρ=0.11)**. OpenEvolve and ShinkaEvolve with fixed opponent pools overfit (0.78 seen vs 0.34 unseen).
2. **LLM agents left to improve bots on their own are weak.** CodeClash (Battlesnake, Halite, and MIT Battlecode 2025 arenas): no model gets through a ladder of human-written bots, and agents invent causes for their losses and ship untested edits. The harness has to own the stats, validation, and population; the agent should only propose code.
3. **Heuristic contests show the upside.** Sakana's ALE-Agent took 1st of 804 humans in AHC058 (4 h, ≈$1.3k, ~4.8k LLM calls, ~100s–1000 scored candidates). The recipe was a fast exact simulator, a tree of candidate programs, injected domain knowledge (simulated annealing, beam search, eval design), and a step that summarises results into insights. Refining candidates step by step beat independent sampling by ~300 performance points. Its main failures were time-limit overruns and not understanding its own code's complexity, which maps onto our 100M CPU-point budget.
4. **Code is better than an LLM at play time, and code that feeds a search beats a pure rule list in adversarial games.** Examples: AutoHarness (code policies beat GPT-5.2-High on 1-player games but struggle on 2-player ones), Code World Models + MCTS, Strategist, and 2048 value-function evolution beating meta-prompting. Let the LLM mostly evolve eval/heuristic functions inside a verified skeleton (legal filter, flood fill/Voronoi, short lookahead), in the style of FunSearch on Hash Code.
5. **Top human Battlecode teams (2025–26) did not use LLM loops.** Only one of the 9 write-ups I read used AI for code (a mid-table solo competitor). Winners won through replay analysis and copying top teams' macro. Automated A/B tests against old versions helped (2025 champion; the Nudge runner's 300–500 games per PR with a 95% CI found real regressions), but several authors warn it overfits to self and to specific maps.
6. **Opponent pool design is the main lever**: past champions + archetypes (rush/turtle/greedy) + reimplemented top-ladder bots from public replays + held-out opponents and maps for honest measurement. A PSRO/Nash mixture (CSRO, ASRO) is the principled form. The real ladder is the outer metric.
7. **Budgets that were enough**: 20 iterations × 30 games (HexMachina, barely above a depth-2 minimax), ~90 iterations (AutoHarness), 400 iterations × 3–20 games per opponent (FAMOU). Expect plateaus followed by jumps. Detecting a 5-point win-rate gain needs ~400 games, so screen cheaply and confirm expensively.
8. **Guardrails**: a validity gate for crashes, illegal moves, and CPU over budget before any win-rate eval; structured loss summaries instead of raw logs; one strategy-owning agent (HexMachina's extra roles hurt); rollback and a population (agents recover badly after losses); a freeze-and-gate before each deadline (1, 10, 17 Oct).

---

---

## 1. MIT Battlecode postmortems 2025/2026 (human top teams)

Sources (all PDFs downloaded and extracted with pdftotext, VERIFIED):
- https://battlecode.org/past.html (index; 2025 winner Just Woke Up; 2026 winner "subscribe to caterpillow"; 2026 2nd Generalized Stroke's Theorem)
- 2025: Just Woke Up (1st), confused (2nd), Om Nom (3rd), SPAARK (HS 1st), The Kragle (~27th) — https://battlecode.org/assets/files/postmortem-2025-*.pdf
- 2026: Generalized Stroke's Theorem (2nd), Lorem Ipsum (mid-table, 30–60th), alext "food" (9th–12th) https://www.alext.app/Battlecode_Postmortem_2026.pdf, 3MiceWalkIntoABar https://www.outercloud.dev/blogs/battlecode-2026/

### AI-assisted coding mentions (VERIFIED)
- **Only one** of the 9 write-ups mentions using AI to write code: **Lorem Ipsum 2026** (§10.3): "Battlecode permits AI to be used in it's development ... large parts of my code for earlier bots, and decently sized chunks of code in my final bot was in fact written by AI." AI wrote pathfinding (heavily edited/debugged by the human), combat ("ratnapping") up to V3, helper geometry functions, and comms bit-packing. "All strategy decisions were made by me. All debugging was by me." Also tried "a (vibecoded) tuner to tune constants" which was "too slow on my system and doesn't do well" — abandoned after one day. Placement: hovered 30–60th overall.
- **3MiceWalkIntoABar 2026** explicitly: "none of our code was written by AI."
- No other postmortem (Just Woke Up, confused, Om Nom, SPAARK, Kragle, GST, alext) mentions LLMs/Copilot/Claude/GPT (grep for llm|gpt|claude|copilot|cursor|AI returned nothing relevant).
- **Nobody in the top 3 of either year reports an LLM in the loop, and nobody reports an automated LLM improvement loop.** (VERIFIED absence in the texts read.)

### Automated testing / tuning infrastructure (VERIFIED)
- **Just Woke Up (2025 winner)**: first year using automation. (a) A/B script borrowed from camel_case / jmerle (https://github.com/jmerle/battlecode-2023) that plays new-vs-old on *every map available* and reports per-map W/L/T; then they watch replays of the losses. (b) An **auto-scrimmage script** that logs into the Battlecode site and requests scrims against ~top 20 teams, then reviews losses. Key caveat quoted: "Just because a new bot is worse against your old one doesn't mean it will be worse against other teams." They kept a feature (resurrection strat, defense towers) that A/B tests against old versions said was bad — and it was right in the final. Repo had "57 versions of our bot." Also: "several high ranked teams ... made it all the way to finals without any sort of automated testing."
- **SPAARK (2025 HS 1st)**: `runmatches.py` lineage (copied 2022→2023→2024→2025 between teams). "Scrimmage analysis is still more important than raw win rate against old bots." Also: "running overnight scrims and hoping for a random improvement is actually very productive." Used testing script as a regression gate before last-minute submits ("In both 2023 and 2024 we threw by submitting a last minute change that didn't work").
- **3MiceWalkIntoABar 2026** built "Nudge", an open-source distributed Go game runner: 1.2 games/s across a small cluster, **300–500 games per test in 5–15 min**, every change on a branch + PR, merge only if **95% CI on win rate** shows improvement. First overnight prototype bisected recent commits and found bugs: win rate vs benchmark went "from under 50% ... to close to 70%". But: "making actual fundamental improvements to your bot outstrips any benefit you gain by using a runner"; "I believe we might have been the only team in the finals that used a game runner." Local noise motivated it: "there was often enough local variation to make a bad change look as if it improved the bot."
- **Lorem Ipsum 2026**: staged gate: 1–3 targeted maps → full map set (~7 custom + sprint maps) vs old bots, reject if ~30% win rate, accept at "slightly above 50%" → online scrims in rating band; pull back if worse.
- **alext 2026 (9–12th)**: explicit overfitting warning: "if you use an older version of your bot as a baseline, and accept any change that increases your win rate, you'll see rapid improvements locally, but you might not perform any better against actual teams who use completely different strategies or on new tournament maps." Also: running 20–30 maps at once hides *why* you lost; better to build 3–5 targeted maps per behaviour, then validate broadly. "Don't obsess over win rate. Often your bot's performance will get worse when you first start implementing an idea, only to increase much later."
- **Generalized Stroke's Theorem (2026 2nd)**: macro improved by queuing scrims vs higher-ranked teams, reverse-engineering and copying their macro; "we spent far more time reviewing scrims than actually writing code"; codegen/bytecode obsession overrated.
- **confused (2025 2nd)**: "didn't really do any bytecode optimization"; success from "extensive replay analysis" and a core game philosophy.
- **Om Nom (2025 3rd)**: Jinja templating for codegen/unrolling; noted Elo jumped ~100 once tournament maps were uploaded (map pool matters).

### Takeaways for our loop (INFERENCE)
- Human winners' edge came from replay-driven *strategy* insight + copying top ladder teams' macro, not from compute-heavy tuning. An LLM loop should replicate "watch replays of losses → hypothesise → implement", not just blind mutation.
- The A/B-vs-previous-self harness is standard and cheap; its known failure is self-overfitting and map overfitting. Must add a diverse opponent pool (incl. scraped/reimplemented ladder strategies) and held-out maps.
- Use statistical gates (Nudge used 95% CI, 300–500 games). Regression gate before any submission.

---

## 2. Sakana ALE-Bench / ALE-Agent and AtCoder Heuristic Contests (AHC)

Most relevant analogue: an LLM agent iterating on a *heuristic program* scored by a *local simulator/tester*, under a wall-clock deadline, then scored on hidden cases. Not adversarial (single-player optimisation), which is the key difference from us.

### ALE-Bench paper (arXiv 2506.09050) — VERIFIED (read html v2 + https://sakana.ai/ale-bench/)
- 40 AHC problems (+10-problem lite subset). Sandbox with C++/Python/Rust, local test-case generator and visualizer. **Public eval** on local/provided cases during the run, **private eval** on hidden 50–300 cases afterwards. Score → performance (0–3500) via AHC's rating formula.
- One-shot: best model o3-high averaged perf 1044 (human average 1260).
- **Iterative refinement for 4 h**: every model improved by >400 perf over one-shot; o4-mini-high → 1520 avg perf / 2104 rating (top 11.8%).
- **ALE-Agent** = (1) domain-knowledge prompts (simulated annealing, beam search, evaluation-function design, neighbourhoods, speed tricks) + (2) inference-time scaling: best-first / beam-like tree search over candidate programs. ~**1000 candidate programs per 4-hour problem** (vs >100 for plain o4-mini-high). Result 1879 avg perf, 2222 rating (top 8.6%). The search (method 2) gave most of the gain.
- **Refinement beats parallel sampling**: 150 independent one-shot samples per problem lost to iterative refinement by ~300+ perf. → Sequential feedback-driven iteration matters, not just best-of-N.
- Weakness: long contests (2-week format) much worse relative to humans than 4-hour ones; models have spiky strengths; top humans ≥1600 perf on 56.2% of problems vs 32.5% for o4-mini-high.

### Live contests — VERIFIED from sakana.ai pages
- **AHC046**: 154th of 1000+ (top 16%). **AHC047 (May 2025)**: 21st of 1000+ (top 2%). (https://sakana.ai/ale-bench/)
- Agent did "about 100 revisions" in 4 h vs ~a dozen for humans; "hundreds or even thousands" of candidate solutions.
- **Failure modes (quoted from sakana.ai/ale-bench)**: failed to fix bugs; "repeatedly exceeded the time limit" because it could not analyse its own code's complexity; wasted effort on changes with minimal score gain; struggled on problems needing algorithms other than SA and on "experimental analysis that requires trial and error".
- **AHC058 (14 Dec 2025, 4 h, 804 humans): ALE-Agent 1st place** — first AI win of a live AHC. (https://sakana.ai/ahc058/) Models: GPT-5.2 (high) 2,654 calls + Gemini 3 Pro Preview (high) 2,119 calls; **total cost ≈ $1,300** incl. infra. Loop: "generating multiple programs simultaneously, summarizing those results to generate insights, and utilizing them for subsequent code generation." Found solution: parameterised randomized greedy + novel "virtual power" evaluation heuristic + SA with 3 large-neighbourhood moves + heavily optimised simulator with precomputed tables. Beat the problem setters' intended approach.

### AtCoder World Tour Finals Heuristic
- **AWTF 2025 Heuristic (16 Jul 2025, 10 h, 12 invited humans)**: OpenAI's agent ("OpenAI-AHC", exhibition) finished **2nd** to Psyho (Przemysław Dębiak); OpenAI led for ~7 h; final 1.81T vs 1.65T (~9.5% gap after system tests). Contest page VERIFIED (https://atcoder.jp/contests/awtf2025heuristic, confirms 10 h, OpenAI sponsor) but it does not show the AI result; numbers are from secondary press (e.g. https://doitinpoland.com/polish-programmer-defeats-ai-at-atcoder-world-tour-finals-2025/). Agent internals: UNVERIFIED / not published as far as I found.
- **AWTF 2026 Heuristic (8–9 Jul 2026)**: AtCoder confirms an OpenAI agent exhibition with a "Humanity Prevails Award" (https://atcoder.jp/posts/1394). Press claims OpenAI took **1st by a very large margin** ("more than seven times higher than the best human" — https://officechai.com/ai/openai-completely-demolishes-human-competitors-at-atcoder-2026-after-placing-2nd-last-year/). **UNVERIFIED** from a primary standings page; method not published.

### Takeaways (INFERENCE)
- The winning recipe in heuristic contests is: a fast, exact local simulator + many (hundreds–thousands) scored candidates + tree search over code versions + injected domain knowledge (SA, beam search, eval-function design) + summarise-results-into-insights step. ~$1.3k of API was enough to beat 800 humans in 4 h on one problem.
- Their documented failures map directly onto Battlecode: CPU-budget/time-limit violations (our 100M CPU points per dragon per turn) and inability to reason about complexity → the harness must measure and report CPU usage per turn and hard-fail over-budget candidates.
- Key difference: AHC score is deterministic-ish and non-adversarial. Our fitness is a noisy win-rate vs a population, which needs many more games per decision and an opponent pool.

---

## 3. Google DeepMind: Code World Models (CWM) and AutoHarness

### Code World Models for General Game Playing — arXiv 2510.04542 (abstract VERIFIED via arxiv.org/abs; talk summary via https://snorkel.ai/blog/code-world-models-and-autoharness-for-llm-agents/)
- LLM translates rules + trajectories into an executable Python simulator (transition, legal moves, termination), plus LLM-written **heuristic value functions** and hidden-state **inference functions**; MCTS plans on top. 10 games (5 perfect-, 5 imperfect-info, 4 novel). Matches or beats Gemini 2.5 Pro-as-policy in 9/10.
- Refinement loop scored by **unit tests derived from real trajectories** (% passing = feedback). Talk says ~20 LLM calls for perfect-info games, ~200 for closed-deck imperfect-info (snorkel summary; paper numbers not independently checked).
- Relevance: we already have an exact simulator (the official engine), so the useful bit is "LLM writes value/heuristic functions that a search algorithm consumes" and "trajectory-derived unit tests as cheap feedback".

### AutoHarness — arXiv 2603.03329 (abstract + html VERIFIED)
- Gemini-2.5-Flash writes a code "harness" that filters illegal moves; refinement = **tree search over code hypotheses with Thompson sampling**, node value = legal-move accuracy; a Critic step consolidates ≤5 failed steps of env errors. Avg **14.5 iterations**; 19/32 games <10. Result: zero illegal moves across **145 TextArena games**; motivation: 78% of Gemini-2.5-Flash losses in Kaggle GameArena chess were illegal moves.
- **Code-as-policy variant** (no LLM at play time): value H = 0 if illegal, else 0.5+0.5·reward. Avg **89.4 iterations**, H=0.939. On 16 **1-player** TextArena games avg reward 0.870 vs GPT-5.2-High 0.844, Gemini-2.5-Pro 0.707, GPT-5.2 0.635.
- **Stated limitation**: "it is much harder to learn an entire policy in code form for 2P games" — they needed runtime search (MCTS) for adversarial games.

### Takeaways (INFERENCE)
- ~15–90 refinement iterations with a cheap scalar score is enough for code policies in simple games; adversarial games are the hard case → our bot should likely be "hand/LLM-written evaluation + runtime search" rather than a pure rule list, and the loop should edit the eval/heuristics.
- Thompson-sampling tree over code versions (not a linear hill-climb) is a cheap, proven parent-selection scheme.
- Legality/validity first: a harness that catches crashes, timeouts, over-CPU turns and illegal moves should be a hard gate before any win-rate eval.

---

## 4. Snake-like games: Battlesnake and Kaggle Hungry Geese

### LLM-written Battlesnake: Dave Smith "Claudia" (VERIFIED, https://www.davesmith.dev/posts/battlesnakes-claude/ , code https://github.com/Dave-Smith/Battlesnake-go-2025)
- Human wrote the HTTP routing; Claude (version unstated) wrote all move logic in Go. Loop: prompt → review → run local games with the official Battlesnake CLI "at least 20 times" per change → describe failures back to Claude. Stopped when he "ran out of tokens for a single conversation".
- Behaviour was specified in plain-English strategy rules ("Evaluate dead-ends to avoid tunnels", "Don't be the smallest snake"). Failure seen: snake entered a dead-end tunnel instead of a square freed by a tail; fixed after feedback.
- **No ranking/win rate reported**; author calls it "far from a championship Battlesnake." → Evidence that naive chat-driven LLM snake coding gets a working but weak bot; no automated loop.
- I found **no public, verified report of an autonomous LLM loop producing a top Battlesnake** (searched; UNVERIFIED absence).

### Classic strong Battlesnake techniques (VERIFIED from sources below)
- Depth-limited **paranoid minimax / MaxN** with alpha-beta and iterative deepening under a 500 ms turn budget; evaluation = flood-fill / **Voronoi area control**, health, food distance, length advantage (https://coreyja.com/posts/BattlesnakeMinimax/Minimax%20in%20Battlesnake/ ; FusionSnake README https://github.com/FusionStreak/FusionSnake ; RBC-winning stateless minimax https://github.com/calvinl4/battlesnake-winter-2022). Branching is brutal (coreyja: 5 turns of 2 snakes ≈ 59,049 states).
- Asymptotic Labs claimed "using a desktop GPU to top the global" leaderboard (https://medium.com/asymptoticlabs/battlesnake-post-mortem-a5917f9a3428) — Medium blocked fetch; details **UNVERIFIED**.
- Curated index: https://github.com/xtagon/awesome-battlesnake.

### Kaggle Hungry Geese (2021; 4 geese, 7×11 torus, simultaneous moves — the closest Kaggle analogue)
- 3rd place (Maxwell, VERIFIED via https://speakerdeck.com/hoxomaxwell/kaggle-hungry-geese): behaviour cloning from scraped top-leaderboard episodes → RL → **MCTS at play time** with a small ResNet policy/value net; flood-fill features; trained on A100s + 16 CPUs. Score 1239.1.
- DeNA's HandyRL public baseline: ~1 day on 1 GPU + 64 CPUs (search-result summary of HandyRL/Kaggle notebook; not re-read — UNVERIFIED detail). Winner score ~1312 (UNVERIFIED). 1st/2nd writeups on Kaggle could not be fetched (JS-rendered).
- **INFERENCE**: top Hungry Geese used NN+search, not LLM code; but the recurring ingredients — flood fill, opponent-move modelling, lookahead search on a torus, **imitation of scraped top-ladder replays** — transfer directly. Scraping public replays of top ladder bots to build opponent models is a proven lever and we have public replays.

### Takeaways (INFERENCE)
- For snake-on-grid with simultaneous/sequential moves, the proven code-policy backbone is: legal-move filter → flood-fill/Voronoi safety → shallow adversarial search with a tunable eval. An LLM loop should start from that skeleton and spend iterations on eval terms and edge cases, not rediscover it.
- Our game is sequential by id (not simultaneous), with 64 independent processes and no shared memory, so each dragon's search must be cheap and deterministic; C++ is ~60× cheaper in CPU points, which directly buys search depth.

---

## 5. LLMs evolving code policies for adversarial games (academic)

### HexMachina — "Agents of Change: Self-Evolving LLM Agents for Strategic Planning" (arXiv 2506.04651; abstract VERIFIED, details via html summary)
- Closest academic analogue to our loop: Settlers of Catan (Catanatron engine). LLM agents **evolve a compiled Python player through code refinement + simulation**, separate from a "discovery" phase that induces an adapter to the undocumented engine API.
- Roles: Orchestrator, Analyst, Coder, Researcher, Strategist. **Removing Strategist and Researcher improved results** — "additional agent recommendations often diluted the strategy". Best: 54.1% win rate [51–57% CI] vs AlphaBeta (depth-2 minimax, strongest hand-crafted baseline); Reflexion-style LLM-plays-directly got 16.4%.
- Eval: **30 games per evolution step** vs fixed AlphaBeta; final test 10×100 games. Budget: **20 evolutions (Coder calls) per phase**. Models: small OpenAI coder model + Mistral-large support roles (exact coder model name from html summary, not double-checked).
- Failures: without the discovery phase, players converged to shallow 1-ply "maximise current VP" heuristics; hallucinated code/heuristics needing filtering; inference cost limited trials.
- INFERENCE: tiny budgets (20 iterations × 30 games) produce a player only marginally above a depth-2 minimax baseline. Evaluation was vs one fixed opponent — the overfitting risk alext warns about.

### Code-Space Response Oracles (CSRO) — Google DeepMind, arXiv 2603.10098 (abstract VERIFIED; numbers via html summary)
- PSRO where the best-response oracle is an **LLM writing policy code** (zero-shot, iterative refinement, or **AlphaEvolve**). Candidate code is scored by expected utility vs the current **meta-strategy mixture over the population**; **K=20 PSRO iterations**; refinement budget M=10.
- Games: repeated RPS (1000 rounds; evaluated vs a population of 43 hand-coded bots from RRPS competitions) and repeated Leduc poker (vs CFR+). CSRO-AlphaEvolve beat PSRO with IMPALA RL oracles on population return and exploitability (e.g. poker PopExpl 4.4 vs 58.4).
- Stated limits: scaling to large observation spaces unclear; context length limits for complex states; API cost.
- INFERENCE: the right fitness for an adversarial code loop is "vs a mixture over a growing pool of past/other strategies", with each new candidate a best response to that pool — PSRO structure fits our loop naturally.

### ASRO — Game-Theoretic Co-Evolution for LLM-Based Heuristic Discovery (arXiv 2601.22896; abstract VERIFIED)
- Solver programs vs instance-generator programs as a zero-sum game with growing pools on both sides and LLM best-response oracles. Beats static-eval heuristic-discovery baselines on generalisation/OOD. Point: **static evaluation against a fixed distribution overfits**; co-evolving a curriculum (here: maps/opponents) fixes it.

### COvolve (arXiv 2603.28386; abstract VERIFIED)
- LLM generates both environments and policies as Python; zero-sum game; **mixed-strategy Nash equilibrium over the policy pool as meta-policy** to prevent forgetting. Domains: driving, maze, navigation (not competitive games).

### Strategist (arXiv 2408.10635, ICLR 2025; abstract VERIFIED)
- LLM updates high-level text strategies / value heuristics; low-level MCTS executes; improved via **population-based self-play** without training data. GOPS and Avalon; beats RL and other LLM skill-learning baselines. Supports "LLM edits heuristics, search executes".

### SMAC-R1 / LLM-SMAC (arXiv 2410.16024; abstract VERIFIED)
- LLM (DeepSeek-Coder) writes **decision-tree code** for StarCraft micro from task descriptions, then self-reflects on environment reward; later distilled into Qwen2.5-7B (SFT + GRPO — model training, out of our scope). Scripts transferred to similar maps unchanged. Useful as evidence that LLM-written rule code handles unit micro competently with little exploration.

### Merge and Conquer: 2048 (arXiv 2510.20205; abstract VERIFIED)
- Two setups: "thinker LLM refines strategy prompt for executor LLM" (**little improvement**) vs "LLM refines the **value function of a limited MCTS**" (+473 points/cycle, ρ=0.607). Used **rollback** to avoid regressions. → Editing code consumed by search > meta-prompting.

### FunSearch on Google Hash Code (arXiv 2411.19744, DeepMind; abstract VERIFIED)
- Humans write the heuristic backbone; FunSearch evolves only the **scoring function**. Top percentile on all past Hash Code qualification rounds; beat top human teams on several; also worked on a held-out AtCoder problem. → Constraining the mutable surface to a scoring/eval function is a strong, cheap pattern.

---

## 6. FAMOU — LLM code evolution in an adversarial multi-agent game (MOST DIRECTLY RELEVANT)

"Beyond Static Evaluation: Co-Evolutionary Mechanisms for LLM-Driven Strategy Evolution in Adversarial Games", arXiv 2606.10389 (abstract VERIFIED; ρ=0.11, 0.780→0.342 and gatekeeper mechanism re-checked in the PDF by the main agent).

- Task: MCTF 2026 3v3 maritime capture-the-flag (multi-agent, adversarial). Strategies are **complete 500–1,700-line Python files**; LLM (Gemini-2.5-Flash or DeepSeek-V4-Flash, T=0.8) receives current code + constraints + **per-opponent evaluation feedback** and returns a full new file. Seeded with 4 handwritten heuristic strategies.
- Built on the OpenEvolve / ShinkaEvolve paradigm: **4 islands × 50 programs**, **UCB bandit parent selection**, **400 iterations**, >30,000 LLM calls total, ~10 h per full benchmark checkpoint.
- **Noise finding**: 3-game scores vs 20–40-game scores had Spearman ρ=0.11 (p=0.69) — i.e. **few-game fitness is essentially random**. Fix = hierarchical eval: 3 games/opponent to screen, **20 games/opponent to confirm a champion**. Removing deep eval was the single largest ablation drop (−26%).
- **Evaluator co-evolution**: each new champion enters the opponent pool as a high-weight "gatekeeper" (−13.7% without it).
- **Weakness pressure**: double the weight of the opponent with the lowest win rate (−18.6% without it).
- Results: FAMOU 68.0% win rate, **61.7% vs held-out unseen opponents**; ShinkaEvolve overfit badly (0.780 seen vs 0.342 unseen); FAMOU beat ShinkaEvolve by ~20% and OpenEvolve by ~28% on combined score. OpenEvolve stagnated 65% of the time (3 distinct champions) vs FAMOU 22.5% (10 champions).
- Dynamics: **punctuated equilibrium** — long plateaus then jumps (e.g. 0.377→0.617 at iteration 200). Don't kill a run for plateauing early.
- LLM invented structures absent from seeds: lookahead search, role-locking, EWMA-based interception filter, adaptive repulsion, dynamic defensive retreat.
- Real-world check: **1st in hardware round-robin, 3rd in simulation** at the AAMAS 2026 MCTF competition.
- Authors: "evaluator design may matter more than seed quality."

### Takeaways (INFERENCE)
- This is essentially our problem (multi-agent adversarial game, code strategies, LLM mutations) and gives the concrete recipe: island population + UCB parent selection + screen-then-confirm evaluation + champions join the pool + up-weight the hardest opponent + held-out opponents for honest measurement.
- Order of magnitude that worked: ~400 iterations, 3→20 games per opponent, ~10 opponents (5 held out). Our matches are 0.5–10 s so 20 games × 10 opponents ≈ minutes per candidate on a few cores — feasible, but game budget per candidate is the binding constraint on a shared machine.

---

## 7. CodeClash (Stanford/Princeton, arXiv 2511.00839 v2, May 2026) — LLM agents iterating on bot codebases incl. Battlesnake and MIT Battlecode 2025

VERIFIED by grepping the arXiv html.
- Arenas: Battlesnake, Core War, Halite I, Poker (Husky Hold'em), RoboCode, RobotRumble; MIT Battlecode 2025 (Python) is also supported (appendix B.1).
- Loop: each round = **edit phase** (mini-SWE-agent, ≤30 turns; agent can read logs, write notes/tests, edit its codebase) then **competition phase**; codebase and logs persist across rounds ("codebase-as-memory"). 8 models × 6 arenas × 10 tournaments × 15 rounds = 25,200 rounds, avg 75 min per tournament.
- Head-to-head leaderboard topped by Claude Sonnet 4.5 (overall round win rate ~70%), then GPT-5, o3.
- **Vs humans (CC:Ladder)**: ladders of human-written bots ranked by Elo (RobotRumble 58, Core War 264). **No model completes either ladder**; best RobotRumble rank 57/58, Core War 260/264 (GPT-5 mini); Sonnet 4.5 reached 43/58 and 205/264. Abstract: "top models lose every round against expert human programmers."
- **Failure modes (quoted)**: models "hallucinate reasons for failure or modify code without confirming if these changes meaningfully improve performance"; infer causal explanations "after reviewing only the opening lines of a single log file"; non-Claude models' edits ungrounded in >65% of rounds (o3 ~80%). After one loss, Sonnet 4.5's next-round win probability <1/3 (vs 71% overall); after five straight losses comeback <15%. Codebases get messy: 5/8 models accumulate root-level clutter and throwaway files.
- Most ladder runs end by failing to win the *final* round against an opponent, not by losing the majority — "competitive but unable to consolidate."

### Takeaways (INFERENCE)
- An LLM agent left to "improve your bot" with only raw logs and self-directed testing is far below expert humans. The fix is structural, not model choice: the harness (not the agent) must run statistically meaningful evals, feed back *aggregated, verified* loss evidence, and force validation before acceptance.
- Keep the agent's workspace clean: one bot tree, notes in a fixed file, throwaway scripts deleted or quarantined.
- Build a **human-bot ladder** analogue: collect/reimplement strong public UNSW ladder strategies as fixed rungs and measure progress by rung reached.

## 8. Husky Hold'em Bench (Nous Research; listed at https://neurips.cc/virtual/2025/122372 — venue type unverified) — LLMs design poker bots under compute limits

Sources: OpenReview PDF https://openreview.net/pdf?id=jARUSddVIB (blocked by OpenReview bot check — not read), Nous tweet https://x.com/NousResearch/status/1963371292318749043, CodeClash appendix B.5 (uses it as an arena). What search snippets say (**UNVERIFIED** beyond snippets): models write a Python bot through a **5-stage iterative refinement scaffold**, under **time and memory constraints that rule out brute force/lookup tables**; bots play a **6-player round-robin**, averaged over trials; Claude 4 Sonnet topped the leaderboard; low-ranked models' bots broke rules (min-raise, folding after all-in). Leaderboard: huskybench.com.
- Relevance: same shape as our CPU-budgeted bots; confirms that rule-legality and resource-limit failures dominate weak LLM bots.

---

## 9. Other bot competitions (Halite, Kaggle sims, Lux, CodinGame) — mostly human/RL, little verified LLM work

### Halite III winner (teccles) — VERIFIED README https://github.com/teccles-halite/halite3-bot
- Hand-coded rules, per-ship Dijkstra over the torus map, a principled square-scoring formula, collision-value model; **no ML**. (Search snippets claimed a genetic algorithm — the README actually says parameters came from guessing, manual trials, and **CLOP** (a noisy black-box parameter optimiser; https://www.remi-coulom.fr/CLOP/) with "very mixed results".)
- Testing: new vs previous version in 2p, and 2 copies vs 2 old copies in 4p; occasionally aggressive/passive variants as sparring partners; cloud compute for local games; rewrote Python→Java for speed "and even more important it made my local games faster".
- "Possibly the key skill in this game was analysing replays" — watched losses, followed single ships for a whole life, used logs to show each ship's target, and pulled per-opponent stats from a community stats site.
- Regret: short-term hacks made while chasing the leader made the bot harder to develop later.

### Kaggle
- **NVIDIA Kaggle Grandmasters (Mar 2026, Playground churn — tabular, not a game)**: GPT-5.4 Pro + Gemini 3.1 Pro + Claude Opus 4.6 wrote >600k lines, ran **850 experiments**, and the final stack of 150 models won 1st; a human set the phase structure (EDA→baselines→features→ensembling); k-fold CV was the gate (https://developer.nvidia.com/blog/winning-a-kaggle-competition-with-generative-ai-assisted-coding/, VERIFIED via WebFetch summary). Lesson: agents + fast eval + human-set method + trustworthy validation metric.
- **Kaggle Game Arena** (LLMs choose moves directly, not code): 78% of Gemini-2.5-Flash chess losses were illegal moves (from AutoHarness abstract, VERIFIED). Shows why the bot should be code, not an LLM at play time.
- **Lux AI S1–S3, Kore 2022, Halite (Kaggle) S4**: winners were a mix of RL/imitation learning and rule-based bots; I found **no verified LLM-written winning entries** (UNVERIFIED; didn't read individual writeups — Kaggle writeups are JS-rendered and wouldn't fetch). Lux S3 (NeurIPS 2024) was built around meta-learning across a 5-game match (https://github.com/Lux-AI-Challenge/Lux-Design-S3/).

### CodinGame
- No verified report of LLM-written bots reaching Legend. CodinGame terms forbid being "assisted by a person or a machine" in contests (search-result summary of https://www.codingame.com/rules; not re-read). **Lead**: CodinGame Winter Challenge 2026 was "SNAKEBYTE" (https://www.codingame.com/contests/winter-challenge-2026-exotec) — a recent snake bot contest whose top-player postmortems could be directly useful (not researched here).

---

## 10. Program synthesis / evolution frameworks (brief)

All abstracts VERIFIED via arxiv.org/abs.
- **AlphaEvolve** (DeepMind, arXiv 2506.13131): evolutionary pipeline of LLMs editing code (diffs across whole files), scored by automated evaluators; used for data-centre scheduling, kernels, maths. The CSRO paper (§5) is the game application. Open clones: **OpenEvolve**, **ShinkaEvolve** (Sakana, https://github.com/SakanaAI/ShinkaEvolve; islands, novelty rejection sampling, adaptive LLM ensemble). FAMOU (§6) shows both **overfit to a fixed opponent pool** in adversarial games unless the evaluator co-evolves.
- **FunSearch**: evolves a single scoring function inside a fixed skeleton; Hash Code application in §5.
- **EoH – Evolution of Heuristics** (arXiv 2401.02051): evolves natural-language "thoughts" + code together; beat FunSearch on combinatorial benchmarks with fewer LLM queries. → Keep a written rationale per candidate; it helps the mutation LLM.
- **From Code to Play** (arXiv 2412.04057): LLM-driven evolutionary hill-climbing over game programs on 29 tasks (mini-Atari, Baba Is You, 12 TAG tabletop games in Java); "performance ... depends more on the task than on model size"; bigger models yield more runnable code but not always better solutions; **using several models and taking the best is more reliable than one model**.
- **MicroRTS programmatic strategies**: programmatic (scripted) strategies won the recent MicroRTS competitions; **2L "Local Learner"** (arXiv 2307.04893, IJCAI 2023) picks a small set of *reference opponents* for evaluation that gives a stronger search signal than iterated best response, fictitious play or double oracle, and its synthesiser beat the two latest human-written competition winners in a simulated tournament. Search snippets also say LLM-seeded local search on MicroRTS found better programs faster (source not pinned down — UNVERIFIED). SynProS competition track: https://rubensolv.github.io/synpros-microrts/.
- **Voyager** (arXiv 2305.16291): Minecraft agent with an ever-growing library of **executable code skills**, iterative prompting using environment feedback + execution errors + self-verification. Relevant pattern only: keep verified reusable skill code (pathing, flood fill, comms codec) and have the loop compose it instead of rewriting.
- **Code as Policies / ProgPrompt**: robotics LLM→code policy work; conceptual ancestors only, not game-competitive (not re-read this session).

---

## 11. Implications for the UNSW Battlecode 2026 loop

All INFERENCE, built from the sources above. Section references in brackets.

### Eval loop (the part that decides whether the loop works)
1. **Hard validity gate first** (§2 time-limit failures, §3 AutoHarness, §8 rule breaks): each candidate must build, run N smoke games with no crash, no illegal action, and **no dragon over the 100M CPU-point budget on any turn**. Log each dragon's per-turn CPU peak and p99 and feed them back to the agent; ALE-Agent's documented weakness was not being able to reason about its own runtime.
2. **Screen, then confirm** (§6 FAMOU): 3-game scores were basically noise (ρ=0.11 against 20–40-game scores). Stage 1: a cheap screen (e.g. ~6–10 games per opponent, maps and sides mirrored). Stage 2: only promising candidates get ≥20 games per opponent. Accept with a statistical rule (§1 Nudge: 95% CI over 300–500 games). Back-of-envelope: detecting 55% vs 50% at 95% confidence takes ~380 games; detecting 60% vs 50% takes ~100. Pairing (same map, same seed, swapped sides) cuts variance a lot.
3. **Opponent pool, never just the last version** (§1 alext and Just Woke Up, §5 CSRO/ASRO, §6, §10 2L): include (a) past champions, which join automatically when promoted (§6 gatekeepers); (b) deliberately different archetypes, such as rush/aggressive, turtle/passive, and max-growth (§9 teccles used aggressive and passive variants); (c) **reimplementations of top UNSW ladder bots built from public replays** (§1 GST copied top teams' macro; §4 Hungry Geese used behaviour cloning from scraped top episodes); (d) **held-out opponents and maps** that the loop never optimises against and uses only for reporting (§6: ShinkaEvolve's 0.78 seen vs 0.34 unseen). Weight the pool toward the opponent we currently beat least (§6 weakness pressure; −18.6% without it). A PSRO-style Nash mixture over the pool is the principled version (§5).
4. **Maps**: a broad map set covering sizes 10–64, torus wrap, and dense/sparse pearls, plus a few targeted maps per behaviour for diagnosis (§1 alext). Refresh maps when the official ones change (§1 Om Nom: +100 Elo from tournament maps alone).
5. **Ground truth is the real ladder** (§1 Just Woke Up auto-scrims, SPAARK, Lorem Ipsum): periodically submit the champion and treat ladder Elo and replays of losses as the outer metric. Local self-play win rate is only a proxy.

### Search structure
- **Population, not a single hill-climb**: 2–4 islands, UCB or Thompson-sampling parent choice (§3, §6), with rollback. Refining one candidate step by step beat sampling many independent ones (§2: ~300 perf). Mix both: parallel candidates per generation, each a refinement of a strong parent.
- **Constrain what the LLM mutates** (§5 Hash Code FunSearch, §5 2048, §3 CWM, Strategist): keep a solid hand-verified skeleton (legal-move filter, flood-fill/Voronoi safety, short lookahead, comms codec) and mostly evolve the eval/heuristic functions and strategy parameters. Whole-file rewrites (§6) work too but need the stronger eval harness.
- **Feed the agent aggregated evidence, not raw logs** (§7 CodeClash): per-opponent and per-map W/L, cause-of-death histograms (collision, starvation, timeout), CPU stats, and 2–3 short replay excerpts of the worst losses. CodeClash agents invented loss causes after reading one log's first lines.
- **One agent that owns strategy beats a committee** (§5 HexMachina: dropping the Strategist and Researcher roles improved win rate). Keep a written rationale per candidate (§10 EoH) and a short running "lessons" file. Keep the workspace clean (§7).
- **Use several models if cheap** (§10 From Code to Play; ALE-Agent AHC058 mixed GPT-5.2 and Gemini 3 Pro).

### Budgets that were enough elsewhere
| System | Iterations / candidates | Games per eval | Result |
|---|---|---|---|
| ALE-Agent AHC047/058 | ~100 revisions, ~1000 candidates in 4 h; ~4.8k LLM calls, ≈$1.3k | deterministic local tests | 21st/1000, then 1st/804 |
| FAMOU (CTF 3v3) | 400 iterations, 4×50 islands, >30k LLM calls | 3 to screen, 20 to confirm, 10 opponents | 68% WR, 61.7% on unseen opponents, won the hardware round |
| HexMachina (Catan) | 20 Coder calls per phase | 30 per step, 1000 final | 54% vs depth-2 minimax |
| AutoHarness code-policy | ~90 iterations | env reward | beat GPT-5.2-High on 1-player games; 2-player games were hard |
| CSRO | 20 PSRO iterations × ≤10 refinements | vs population mixture | beat RL-PSRO on RPS and poker |
| Nudge (human, Battlecode 2026) | per PR | 300–500 games, 95% CI | found regressions: <50% → ~70% vs benchmark |

With 0.5–10 s matches, 400 games per decision is roughly 3–70 core-minutes. On a shared, loaded machine that's the real limit, so screen hard, run C++ bots (cheaper CPU points and faster local games, §9 teccles), and parallelise within whatever load cap applies.

### Pitfalls to design against
- **Overfitting to self or to a fixed pool** (§1 alext, §6, §5 ASRO). Mitigate with held-out opponents and maps, champions joining the pool, and ladder checks.
- **Noise read as signal** (§1 3Mice, §6). Mitigate with confirm-stage evals and CIs; never accept on a handful of games.
- **Ungrounded edits and hallucinated loss causes** (§7). Mitigate with structured loss summaries and a rule that every hypothesis names the evidence it is based on.
- **Poor recovery after losses** (§7: comeback <1/3 after one loss). Mitigate by having the orchestrator, not the agent, keep the population and branch from earlier champions.
- **Plateaus that are really punctuated equilibrium** (§6). Don't stop a run early just because it's flat; add diversity/novelty.
- **Last-minute regressions** (§1 SPAARK threw 2023/2024). Freeze-and-gate before each deadline (1, 10, 17 Oct).
- **Chasing the current leader with hacks** makes the code hard to evolve (§9 teccles regret). Keep the skeleton principled.
- **Human strategic insight still wins** (§1: all top teams were driven by replays; §7: models lose to expert human bots). Budget human (or orchestrator) replay review of top ladder games each cycle to seed hypotheses and new pool opponents.

---

## Out of scope / leads (would be filed as GitHub issues, not pursued here)
- CodinGame Winter Challenge 2026 "SNAKEBYTE" (https://www.codingame.com/contests/winter-challenge-2026-exotec): a recent snake bot contest. Top-player postmortems could be directly useful for our snake mechanics.
- Kaggle Hungry Geese 1st/2nd place writeups (JS-rendered, couldn't fetch): worth a browser pass for torus-snake search/eval details.
- Asymptotic Labs Battlesnake GPU postmortem (Medium, Cloudflare-blocked).
- Husky Hold'em Bench paper (OpenReview blocked): details of its 5-stage refinement scaffold under compute limits.
- FAMOU code release (link in arXiv 2606.10389 abstract): possibly reusable harness code (islands + co-evolving evaluator).
- CodeClash is open source and already has a Battlesnake arena and an MIT Battlecode 2025 arena; its harness could be adapted to UNSW Battlecode for a fast start.
- Nudge distributed Battlecode runner (open-sourced by 3MiceWalkIntoABar, linked from https://www.outercloud.dev/blogs/battlecode-2026/): design reference for a match farm.
- Lux AI S3 / Kore 2022 top writeups: not read.
- AWTF 2026 Heuristic official standings and OpenAI's method: unconfirmed from primary sources.
