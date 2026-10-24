# Process and tooling (incl. ML / RL / LLM attempts)

**Scope.** First, what separates winners in how they work: scrims, harnesses, map pools, replay analysis, copying the meta, and tuning. Second, every ML, RL, evolutionary and LLM attempt found.

**Sources and checks.**
- The detailed sections below were researched by a helper subagent and assembled by the lead.
- The lead checked the CodeClash figures against the paper's full text (arXiv 2511.00839): 46% hallucinated loss causes for Sonnet 4.5 on BattleSnake, 56% of rounds validated, and no round won against the expert human bot.
- The lead checked the 2026 winner's repo, github.com/awu7/battlecode-2026. It has `CLAUDE.md` rules per bot folder and `autoscrims/ABtest.py`, which runs 8 workers by default with both sides per map, reps, a fixed-seed option and a two-sided binomial p-value.
- Per-team details also appear in postmortems.md.

## Summary: the winners' toolchain, as a spec for our harness

Recurring elements across 2022–2026 top teams:
- Producing Perfection, 1st in 2023
- Gone Sharkin', 1st in 2024
- camel_case, 2nd in 2024
- Just Woke Up, 1st in 2025
- subscribe to caterpillow, 1st in 2026

Each item below is cited here or in postmortems.md.

| Element | What the winners did | Our version (inference) |
|---|---|---|
| **A/B runner** | New vs old on every map, both sides, run in parallel, with replays saved. PP ran it in CI on every PR and posted results as a PR comment; 4 Musketeers ran it in GitHub Actions because local runs "renders that person's computer useless". | Our engine runs matches in 0.5–10 s. Run a few hundred games per candidate on a CI or cloud runner, not the shared dev machine. |
| **Significance gate** | caterpillow printed a binomial p-value. 3MiceWalkIntoABar merged only when the gain cleared its 95% confidence bound. Lorem Ipsum rejected ~30% and accepted "slightly above 50%". | The agent loop merges only when the gain clears the gate against *the whole pool*. Log p-values so marginal changes can be re-tested later (XSquare §5.8: don't discard near-50% changes without checking for bugs). |
| **Version snapshots** | wololo kept ~65 feature-named snapshots and JWU 57 versions in 25 days. camel_case had `duplicate.py`, and caterpillow froze snapshots per tournament. | Every accepted champion is frozen and named after its one change. |
| **Opponent pool** | Sparring bots were built to stress one behaviour: Gone Sharkin' `rushbot`, `nothingbot`; future perfect 2016 `felix15`, `supercowpowers`; Baby Ducks' rush bot. Big Red reproduced the top bot, and Oak 2019 lost to a stale opponent mix. | Pool = past champions + a rusher + a turtle + clones of the top ladder teams built from public replays, re-weighted as the ladder meta shifts. |
| **Map library** | caterpillow kept 72 maps plus ~20 scenario maps (`starvation`, `trapped`, pathing tests) and a skip list of troll maps. Gone Sharkin' used every past tournament map. PP converted the previous year's maps. food used 3–5 purpose-built maps per behaviour, then a broad sweep. | A map generator for 10–64 sizes, kelp mazes, portals, sparse/dense pearls and all 3 symmetries. Keep a held-out set the loop never tunes on. |
| **Automated scrims vs real teams** | JWU scripted scrims against the top ~20. caterpillow 2025 had `scrimmer.py` for the top 15. In 2024 the top 3 ran ~2.5× the median scrim volume, mostly unranked. | Use the UNSW JSON API (60 manual games/h) to scrim the top ~10 on chosen maps every few hours and pull the replays in. |
| **Replay analysis** | GST: "far more time reviewing scrims than writing code". Confused reached #1 through replay analysis. The 4 Musketeers had a dedicated replay analyst. | Build a replay parser that labels each death (head-on / body / kelp / self), its turn-order context and length, and the pearls dropped. Agents must cite concrete turns (CodeClash failure mode). |
| **Early termination** | camel_case `compare_fast.py` stopped a match after 100 rounds of map control. | Stop when one side is wiped or holds an unassailable length or count lead, to raise throughput. |
| **Codegen** | Schema-generated comms (smite → 4 Musketeers → Gone Sharkin'), Jinja-unrolled loops (Om Nom, caterpillow). | Generate the sonar encoder/decoder from a schema in every bot language. Humans and agents edit readable sources. |
| **Written rules for AI agents** | caterpillow's `CLAUDE.md`: new behaviour only as new strategy classes; refactors must keep behaviour identical; clean AI code before commit; use team helpers. | The loop's playbook should state the same kinds of rules, and the harness should enforce them (for example a behaviour-identical refactor must produce identical replays on fixed seeds; *inference*). |

## ML/RL/LLM attempts

Summary (inference, from everything below): no documented Battlecode finalist has won with ML, RL, neural nets or evolved code. Every published attempt placed far below hand-written heuristic bots or failed outright. Automated *parameter tuning* on top of hand-written bots is the one ML-adjacent idea top teams mention, and even that is rarely reported to work. LLM coding help is reportedly permitted (one 2026 postmortem says so; no official MIT rule found — UNVERIFIED); LLMs help write infrastructure code, but postmortems don't credit them with strategy wins.

### 2026 — waffle (Tanner Winkelman; finalist 2020 and 2021 per his README) — linear genetic programming — did not qualify for any tournament
- Source: https://github.com/winkelmantanner/battlecode26_waffle_genetic (README)
- What: bot logic written in a custom assembly-like instruction language (128 memory slots per type, jump/marker instructions run by a switch-in-while-loop interpreter in Java); genome stored as JSON. Mutations: insert and delete only. Population of **two**: the current best and one mutant, played against each other "until it either rejects or accepts the mutant" — about one mutation tried per match. Exceptions are selected against.
- Result (fact): "The evolutionary algorithm produced only one piece of beneficial development that I incorporated": building rat traps. "Beneficial mutations were rarer than I expected." The bot "was not strong enough to appear in any of the tournaments ... So overall this project was a failure, which I expected ... Applying an evolutionary algorithm to Battlecode is something that I had been thinking about for years, but I knew it was a bad idea."
- Why (his reasons): little time, and writing strategy in the invented low-level language was a handicap. (Inference: the search space of raw instructions is too large for ~1 evaluation per match; the one useful discovery was a single discrete behaviour, not a tuned number.)

### 2024 — Oskar Kraak — genetic programming of Java bots — failed vs the example player
- Source: https://github.com/oskarkraak/battlecode24-genetic-programming (README)
- What: templates with mutable action lines, crossover + mutation; single-match knockout per generation for speed.
- Result (fact): population 40 × 500 generations "failed to win against the random 'examplfuncsplayer' even a single time in 10 matches ... **genetic programming is not adequate for Battlecode**."
- Why (inference): single-match fitness is extremely noisy; random code rarely reaches even basic competence (moving, gathering) so there is no gradient.

### ~2016–2020 — TheSimpleSoldier — GA-evolved neural network for fight micro
- Source: https://github.com/TheSimpleSoldier/FightMicroGA ("Runs a genetic algorithm with a battlecode simulator to create a neural network for running fight micro."; last updated 2020-03-26). "The Simple Soldier" was a strong 2016 team (future perfect imitated its strategy: https://github.com/TheDuck314/battlecode2016).
- Result: README reports none; no evidence it was used in a submitted bot. UNVERIFIED whether it ever competed.
- (Inference: the narrow target — micro only, in a custom simulator — is the sensible scope for any learning; still no reported success.)

### 2022 — ECLAIR-Robotics battlecode-gym — RL environment
- Source: https://github.com/ECLAIR-Robotics/battlecode-gym ("to learn about reinforcement learning paradigms !"). No results reported; educational. UNVERIFIED whether any bot came from it.

### Practitioners on why ML doesn't work in Battlecode
- Stone Tao (4-time finalist, later creator of the Lux AI Kaggle competitions — lead, not verified here): "it's usually near impossible to use advanced AI algorithms due to a whole host of reasons ... Every single bot submitted to Battlecode (that performs well) is effectively a giant, carefully curated, grown, and pruned, decision tree." https://blog.stoneztao.com/posts/bc21/
- Reasons visible across postmortems (inference, each grounded): (1) per-unit compute caps (bytecode ~7.5k–15k per turn: agrebe 2020/2021, JBW 2020) make even neural inference expensive; (2) specs change mid-season (Baby Ducks 2021: slanderer ROI, buff formula; MFC 2021; smite 2019 "rushes were obsolete in a day"), invalidating training data; (3) the meta and map pool shift, and tournament maps are adversarial (Stone Tao 2020, MFC 2021, wololo 2021); (4) ~3–4 weeks and limited compute per team; (5) the decisive gains come from discrete strategic ideas copied from replays, not continuous optimisation (High Ground 2020, confused 2025).

### Automated parameter tuning on hand-written bots
- The High Ground 2020 (4th): wanted to tune `MagicConstants.java` but couldn't "robustly given we could only play against bots we had" and the bot kept changing. https://battlecode.org/assets/files/postmortem-2020-the-high-ground.pdf
- Gone Fishin' 2023 (2nd per past.html): "A slightly improved macro strategy might increase our win rate by 5%, but micro can do a 30%-50% increase. We learned the hard way, tuning hyperparameters and playing against an older version of the bots a million times." Their macro choices were also decided "in self-play." https://battlecode.org/assets/files/postmortem-2023-gone-fishin.pdf
- Lorem Ipsum 2026 (finalist): a "(vibecoded) tuner to tune constants" was abandoned — "too slow on my system and doesn't do well." https://battlecode.org/assets/files/postmortem-2026-lorem-ipsum.pdf
- Malott Fat Cats 2021: "Our build order ... was surprisingly hard to improve, in the sense that we couldn't get a considerable winrate against our old bot by only changing build order"; the final balance was the one with best win rate vs old bots on old maps — which then failed on new finals maps.
- (Inference: tuning is limited by eval noise, compute, and a non-stationary target. Where it helps, it's small, discrete choices evaluated on many maps vs several opponents.)

### 2025–2026 — CodeClash benchmark (Stanford/Princeton et al.) — LLM agents iteratively improving competitive bots, including MIT Battlecode 2025 and BattleSnake
- Source: https://arxiv.org/abs/2511.00839 (v2, 12 May 2026; PDF read in full-text via pdftotext)
- What (fact): LMs play multi-round tournaments. Each round: an agent (mini-SWE-agent) edits its own codebase, then codebases fight in an arena; logs are copied back into the codebase as the only feedback; the codebase is the agent's only memory. 8 models × 6 arenas (including **BattleSnake** and **MIT Battlecode 2025** via Python stubs), 1,680 tournaments, 25,200 rounds. Each round's winner decided over 1,000 simulations (Battlecode 2025 was the one deterministic arena).
- Results (facts, quotes):
  - "top models lose every round against expert human programmers ... the best model (Claude Sonnet 4.5) fails to win a single round against an expert human-written bot." On human-solution ladders, best reached rank 57/58 (RobotRumble) and 260/264 (Core War).
  - **Poor log analysis:** "despite explicit suggestions to write analysis tooling in the prompt, most LMs do not extract worthwhile information, often stopping at reading the first lines of a log file or calculating the win rate of the last round."
  - **Hallucinated loss causes:** agents infer why they lost "after reviewing only the opening lines of a single log file, when these lines do not even show the deciding moment." Claude Sonnet 4.5 did this in >17% of rounds on average and **46% of rounds in BattleSnake** (Sonnet 4: 34%).
  - **Untested deployments:** "most models deploy untested code"; only Claude Sonnet 4.5 validated changes in a majority of rounds (56%); GPT-5 50%; Gemini 2.5 Pro and o3 ~20%. "Combining exploratory methods with self-play could likely avoid unwanted regressions."
  - **No recovery after losses:** Claude Sonnet 4.5's next-round win probability after one loss was < 1/3 (vs 71% overall); after five straight losses < 15%. "An inability of models to reconsider strategies."
  - **Codebase rot:** repos become "progressively messy and redundant", e.g. files like `analyze_round_13_v2.py`.
  - **Opponent code access didn't help much:** Claude read the opponent's code in 99.3% of rounds but its win rate moved −1.8%; GPT-5 read it 12.8% of rounds and gained +7.8%.
  - **Positional (side) advantage:** Battlecode 2025 was the only arena with a first-player/side advantage; fixed by randomly shuffling player order.
- Why it matters here (inference): this is the closest published analogue to our planned Claude-agent loop, on a Snake-like arena. The failure modes are process failures — ungrounded log reading, no A/B validation, no strategic pivot, clutter — exactly the practices human winners got right (A/B scripts, replay review, copying the meta, keeping the code clean). The loop has to enforce those in the harness, not trust the agent to do them.

### 2026 — Metta-AI cogame-battlecode — LLMs write strategy "doctrines" for a ported Battlecode engine
- Source: https://github.com/Metta-AI/cogame-battlecode (README)
- What (fact): deterministic Nim ports of several Battlecode rule sets (2016, 2019–2026). "Neither cog moves a rat. At t=0 each writes a doctrine — a sealed JSON sheet of named knobs" and the engine plays best-of-three. "A policy is just a prompt": LLM policies (`battlecode-loyalist`, `battlecode-opportunist`) vs scripted baselines, including "the strong distilled-awubot baseline" and the deliberately weak ported `examplefuncsplayer`. Search results show it calls Claude Haiku 4.5 via a local endpoint (UNVERIFIED beyond search-snippet text).
- Results: none reported in the README. Not a competition entry.
- (Inference: an LLM choosing values for named knobs of a hand-written bot is a much smaller, safer search space than LLM-written code — a useful pattern for part of our loop.)

### MIT's own stance on ML and AI tools
- battlecode.org FAQ (fetched 2026-09-23): "Machine learning has not historically been used in Battlecode, since computation is limited and the game is a highly complex environment. Rather, Battlecode is about more traditional AI involving pathfinding, resource management, communication and combat tactics ... However, we'd be thrilled and excited to see a successful implementation of machine learning in Battlecode!" https://battlecode.org/about.html
- LLM coding assistants: Lorem Ipsum's 2026 postmortem states "Battlecode permits AI to be used in it's development." I did not find an official MIT rules page stating an LLM policy — **UNVERIFIED** from a primary source.

### 2026 — Lorem Ipsum (Nuaym Syed, solo) — the only postmortem found that documents LLM-written code — finalist, lost 2nd finals match 3–2 (self-reported)
- Source: https://battlecode.org/assets/files/postmortem-2026-lorem-ipsum.pdf §10.3 "AI"
- What (fact): "large parts of my code for earlier bots, and decently sized chunks of code in my final bot was in fact written by AI" because he was solo and short on time. AI wrote: pathfinding (bugnav, later a one-step BFS), attack/"ratnapping" micro up to V3, trivial helpers (e.g. `canGoStraight`), and comms bit-mask encode/decode. "All strategy decisions were made by me. All debugging was by me."
- Outcome (fact): "Our vibecoded attack micro since Sprint 1 (has not changed at all) got destroyed in almost all head to head combat scenarios"; "Our pathfinding seemed to suck." Ladder ~30th–60th; still qualified for finals.
- (Inference: LLMs sped up standard plumbing (encoding, helpers) but their game-specific micro was weak and never iterated — consistent with CodeClash's findings.)

### Counter-example: a 2026 finalist that used no AI and built a runner — 3MiceWalkIntoABar (Liam Hanrahan et al., freshmen) — finalist via US qualifiers, "knocked out rather quickly" (self-reported; postmortem listed on past.html)
- Source: https://www.outercloud.dev/blogs/battlecode-2026/ ; runner https://github.com/outercloudstudio/nudge
- Stated rule: "none of our code was written by AI."
- Included here for its process evidence (see Process section).

---

## Process and tooling

### 2025 — Just Woke Up (Tim Gubskiy, Andy Nguyen) — 1st (past.html)
- Source: https://battlecode.org/assets/files/postmortem-2025-just-woke-up.pdf
- **A/B test script across every map.** "I always had some sort of versioning in past years ... but I was always going off of vibes ... not very scientific. This year, I 'borrowed' a script that was circulating in the Discord from the team camel_case (https://github.com/jmerle/battlecode-2023/tree/master) that enabled me to AB test two bots on every single map." Output = per-map win/loss/tie, then he watched the specific replays to learn why.
- **Self-play win rate is not the whole truth:** a confident change sometimes lost to the old bot; "Just because a new bot is worse against your old one doesn't mean it will be worse against other teams. That's where my second script came in."
- **Automatic scrimmaging script:** logs into the Battlecode website and requests scrims against up to ~top 20 teams (those with unranked scrims open), then reviews replays of losses and of wins against stronger teams — "an insanely valuable learning experience."
- Finals: minor optimisations that "greatly boosted our winrate against our old bot versions" plus targeted fixes vs the two teams they struggled with in scrims (confused, Om Nom).
- Advice: "Stuff like automation scripts though feel free to just steal"; read other teams' past code but understand what you copy; "Just running your bot against itself works alright but it only tests your bot against the strategy that you have managed to think of"; "Do automation maybe" — "several high ranked teams ... made it all the way to finals without any sort of automated testing."

### 2025 — SPAARK (Maitian Sha, Suvanth Erranki, Erik Ji, Jie Gao; high school) — HS 1st 2025 (past.html); self-reported 3rd in 2024 and 5th/6th in 2023
- Source: https://battlecode.org/assets/files/postmortem-2025-spaark.pdf
- **The `run_matches.py` lineage:** thanks "SPAARK for their runmatches.py script from 2024 (which was copied from 4 Musketeers' run_matches.py script from 2023 (which was copied from Producing Perfection's run_matches.py script from 2022))". Producing Perfection won 2023 (past.html). The local match-runner is a shared, inherited tool among top teams.
- "Scrimmage analysis is still more important than raw win rate against old bots." "Running overnight scrims and hoping for a random improvement is actually very productive" (they show overnight run tables).
- **"DON'T make last-minute changes. In both 2023 and 2024 we threw by submitting a last minute change that didn't work. If you really want to make a last minute change, use a testing script and make sure it isn't losing."** The testing script caught a bug 5 minutes before the HS deadline.
- Build first: "navigation, micro, code organization, testing/auxiliary scripts, creating a bunch of testing maps" — then adopt the top teams' macro once the post-Sprint meta settles.

### 2026 — food (Alex Thummalapalli, solo, first year) — "top 12 at the Battlecode 2026 Finals" (self-reported; listed on past.html without placement)
- Source: https://www.alext.app/Battlecode_Postmortem_2026.pdf
- **Overfitting warning (directly relevant to an automated loop):** "if you use an older version of your bot as a baseline, and accept any change that increases your win rate, you'll see rapid improvements locally, but you might not perform any better against actual teams who use completely different strategies or on new tournament maps."
- **Big map sweeps hide the signal:** running 20–30 maps at once "was a mistake as it's hard to learn anything from why your bot suddenly lost on some arbitrary map." Recommends 3–5 purpose-built maps per behaviour (e.g. combat micro), watch replays while iterating, then a broad sweep at the end. Acknowledges the trade-off: few maps → overfit; many maps → insight "lost to a simple win rate comparison."
- "Don't obsess over win rate. Often your bot's performance will get worse when you first start implementing an idea, only to increase much later." Step through individual robot turns and compare to what a human would do.
- Ignore sprint/leaderboard: first-week rushes "will rarely work by the Final tournament"; build reusable systems (economy, exploration, pathfinding) first. Derive behaviours from a simple core principle.
- Reverse-engineer high-ranking teams via scrims.

### 2026 — Lorem Ipsum (Nuaym Syed, solo, first year) — qualified for finals via US qualifiers, lost 2nd finals match 3–2; ladder ~30–60th (self-reported)
- Source: https://battlecode.org/assets/files/postmortem-2026-lorem-ipsum.pdf
- **Testing protocol (3 stages):** (1) new vs old bot locally on 1–3 maps where the change should matter most; (2) vs old bot(s) on all maps (~7 custom maps + Sprint maps) — reject if ~30% win rate, accept if "roughly slightly above 50%"; (3) upload and scrim many teams in the same rating range, pull back if worse. Focused on "analyzing macro and micro strategies rather than the overall score ... since often minor changes do not significantly increase bot's performance."
- A "(vibecoded) tuner to tune constants" was abandoned: "too slow on my system and doesn't do well" (see ML/LLM section).

### 2023 — 4 Musketeers (Winston Cheung, Maxwell Jones, David Lyons, Bharath Sreenivas) — 3rd (past.html)
- Source: https://battlecode.org/assets/files/postmortem-2023-4-musketeers.pdf
- **Offloading match runs to CI.** "In the past ... one of the teammates run that version against all previous versions locally. When there are > 30 maps, this can take hours and renders that person's computer useless." They adopted Producing Perfection's (2023 champion) script "to run players against each other using Github Actions", producing a match summary vs a specified older version. (Inference: matches the user's machine rule — run the eval matrix in CI, not locally. As of 2026-09-26 the loop instead spreads games over this Mac, under its load gate, and sig's SSH game machines.)
- **Generated comms code:** their sector bit-packing across 16-bit array slots "required an automated approach. Otherwise, changing something in the middle would create days worth of side effects to fix" — adapted smite's autogenerated comms code from 2022; added a write buffer ("buffer pool") to avoid write amplification (writes cost 100 bytecode).
- Lost a winners-bracket qualifier match "due to a map exploiting an extremely rare bug."

### 2025 — Om Nom (Cyril Sharma, Egor Gagushin) — 3rd (past.html)
- Source: https://battlecode.org/assets/files/postmortem-2025-om-nom.pdf
- **Templated code generation with Jinja** instead of a custom compiler: write loops in templates that expand to unrolled Java, so "we could just write it in the optimized way immediately." (Inference: a Claude loop can edit readable templates while the generated code stays bytecode-optimal — if our engine has a compute budget.)
- **Cheap, big wins found by comparing to old bot:** fixing units that exceeded bytecode on their first turn (allocating big arrays) "gave us a substantial win rate against our old bot."
- **Reverse-engineered the #1 ladder bot's rush, then discarded it** — high variance ("when it didn't [work], we were almost guaranteed to lose").
- Won US quals by bigger margins than scrims suggested; ELO jumped ~100 once tournament maps were added to the ladder pool — maps shift results.

### 2023 — camel_case (GitHub user jmerle; real name UNVERIFIED) — 9th–12th in 2023 finals; 1st–2nd in international qualifiers (self-reported in README)
- Source: https://github.com/jmerle/battlecode-2023 (README + `scripts/`). This is the script Just Woke Up (2025 champion) "borrowed". jmerle also publishes per-year statistics sites (https://jmerle.github.io/battlecode-2023-statistics/, https://jmerle.github.io/battlecode-2024-statistics/, linked from past.html).
- Facts from the code:
  - `compare_full.py`: runs player A vs B on every server map, **both sides (as red and as blue)**, via `gradlew run -PteamA -PteamB -Pmaps=...` in a multiprocessing pool, saves replays per run, prints per-map winners.
  - `compare_fast.py`: streams the match over a websocket, runs 4 matches concurrently (`asyncio.Semaphore(4)`), and **kills a match early once one player holds "map control" for 100 consecutive rounds**, recording outcome `MAP_CONTROL` — an early-termination heuristic to cut eval cost. Prints a per-map table of "As red / As blue" results.
  - `duplicate.py`: copies a bot package to a new name and rewrites package/imports — the versioning mechanism (every tournament bot kept as its own package in `src/`).
- (Inference: side-swapping matters on symmetric maps; early termination on a decisive state is the main lever for eval throughput.)

### 2025 — confused (Michael Hahn "skipiano", solo) — 2nd (past.html); top seed entering finals
- Source: https://battlecode.org/assets/files/postmortem-2025-confused.pdf
- **Replay analysis over algorithms:** "I didn't really do any bytecode optimization and I wouldn't say any of the algorithms I had were very hard to implement. My approach involved extensive replay analysis, borrowing strategies that looked good from other teams, and a focus on aligning all development with a well-defined core game philosophy." Named four teams as "my primary source of effective strategies."
- Measured progress vs his own frozen qualifier submission: one feature (tower flickering) gave "a 70%+ win rate against my submission for qualifiers."
- Scrims vs other finalists hovered ~50%; lost the final on a large map with corner ruins that his center-biased explorers missed (map-specific weakness).

### 2019 — Oak's Last Disciple (Ivan Geffner "XSquare" et al.) — 33rd–48th (self-reported via XSquare's later PDFs); did not make finals
- Source: https://battlecode.org/assets/files/postmortem-2019-oak.pdf
- **Test bots simulating specific opponents:** "We tested this against a bot that simulated DOS strategy with very good results. We also tried against a bot that would increase the size of every wave." Imitated NP-cgw's attack protocol from watching it ("I don't know how was theirs, but we could simulate it fairly well").
- **The failure mode of a stale opponent pool:** they chose the "most consistent" bot assuming an even mix of strategies; by finals "there were almost no rush bots left and the more conservative bots were heavily punished." "Our biggest mistake ... we barely scrimmed after seeding (games took too long and we were really lazy about it), and we didn't see the meta shift that was happening right in front of our eyes." (Inference: the opponent pool's composition must track the live meta, not a uniform mix.)

### Earlier-year process evidence (full detail in postmortems-2014-2021.md)
- **Imitation sparring bots, versioned by date** — future perfect 2016 (1st): `src/` holds `supercowpowers*`, `felix15`, `simplesoldier18`, turtle bots; `felix15` was "a frequent test opponent." https://github.com/TheDuck314/battlecode2016
- **Version-vs-version tournament CLI** — Arbitrary Graph Restoration Fund 2017 (1st): `./run -a vA -b vB --tournament` over all maps, `./backup tag`. https://github.com/HalfVoxel/battlecode2017
- **Copy the leader from replays** — The High Ground 2020 (4th) "pulled up some replays of Java's games and worked on a new bot for several days until we imitated it as closely as possible" → #1 on ladder. https://battlecode.org/assets/files/postmortem-2020-the-high-ground.pdf ; McGlynn 2015 (1st): "frequently scrim against the top teams and copy their strategies."
- **Tuning blocked by opponent diversity** — High Ground 2020: constants in `MagicConstants.java` never tuned because "we could only play against bots we had" and the bot kept changing.
- **Self-play on the wrong maps** — Malott Fat Cats 2021 (4th, #1 on ladder for the whole second half) picked the econ/aggression balance with "the most winrate against our old bots on the maps used for Sprint and Sprint 2"; finals maps were huge/low-passability and "relying on previous maps turned out to be a pretty bad idea." https://battlecode.org/assets/files/postmortem-2021-malott-fat-cats.pdf
- **Scrims as bug detector** — smite 2019 (1st): "every single loss could be traced to a major bug, and we found bugs in most of our wins too"; ≥60% vs every finalist, 141–33 record. 3 Musketeers 2021: frame-by-frame review "against other teams or against old versions of your bot ... (even in wins)."
- **Using others' auto-accept scrims as a test pool** — wololo 2021: tested "very often" against monky and 3 Musketeers who left auto-accept on.
- **Secrecy vs testing trade-off** — High Ground 2020 didn't scrim its final change vs rivals, so rival-specific bugs went unseen; Battlegaode 2020: early on "the benefit of testing your strategy usually outweighs the disadvantage of leaking it."

### 2026 — 3MiceWalkIntoABar (Liam Hanrahan, Richard Xun, Paul Fang Li, Armaan Gomes; freshmen) — finalist via US qualifiers, out early (self-reported)
- Sources: https://www.outercloud.dev/blogs/battlecode-2026/ ; https://github.com/outercloudstudio/nudge
- **Local noise made bad changes look good:** "when testing locally, there was often enough local variation to make a bad change look as if it improved the bot." Their rating fell from top 10 to ~60th in a few days.
- **Bisecting regressions with a runner:** the first prototype runner, built in one night, was run over "all the recent commits and isolated those that reduced the win rates" → bugs found → win rate vs benchmark "from under 50% ... to close to 70%."
- **Nudge** (open-source): distributed runner (Go/Deno/Java, clients over websockets, e.g. via Tailscale). 1.2 games/s on their cluster; 300–500 games in 5–15 minutes. Reports win rate, win-rate delta, and a **95% confidence bound**; "If your win rate delta is less than the error bound, then there isn't enough evidence to suggest one bot is better than the other."
- **Merge gate:** "all changes had to be made on a separate branch accompanied with a pull request ... before we merged any changes, the tester gave us confidence that the change improved the win rate. We used a 95% confidence interval."
- Caveat from the same author: "Using a game runner is NOT necessary at all. Most of the teams in the finals did not use a game runner ... making actual fundamental improvements to your bot outstrips any benefit you gain by using a runner." He believes they may have been the only 2026 finalist using one (UNVERIFIED). Devs reportedly worry runners favour teams with more compute.

### 2026 — Generalized Stroke's Theorem (Urav Tanna, Seth Lifland, Andre Mao) — 2nd overall (past.html)
- Source: https://battlecode.org/assets/files/postmortem-2026-generalized-strokes-theorem.pdf
- **Macro by imitation:** "we would queue scrims against teams above us on the leaderboard, reverse engineer their macro (which is much easier than reverse engineering micro, pathfinding, or communication), copy whatever strategy we gleaned, and if it made us better then we would keep it." Rat-king placement and "magic numbers for how many Baby Rats to build" came from this.

### 2023 — Don't @ Me (George Zhang, Henry Liao, Parum Misri, Ray Guo) — 7th (past.html)
- Source: https://battlecode.org/assets/files/postmortem-2023-dont-at-me.pdf
- "Create a copious number of new packages and new versions, each implementing or testing a new feature" — try ideas in a copy of the current best bot.
- **Two-track evaluation:** "online full-set scrimmages against other teams on the ladder with all-map scrimmages against old versions of our bots on our local clients to ensure that we were making progress consistently throughout the season and to track the state of the meta." 10-match online scrims (enabled that season) beat 3-match sets.
- **"Code > Elo":** generalised solutions over coding for ladder-map specifics "often helped us find wins in tournaments where opponents might have hard-coded heuristics that beat us on the ladder."
- "Sometimes we despaired as our bot started losing to an older version across a set of 30 maps."

### 2025 — The Kragle (Justin Ottesen, Andrew Bank, Matt Voynovich) — placement not given on past.html (UNVERIFIED)
- Source: https://battlecode.org/assets/files/postmortem-2025-the-kragle.pdf (Ottesen also compiled the Battlecode Archive, per past.html)
- Regret: "we did not pay that much attention to other teams' strategies. Copying other teams' strategies is a Battlecode staple because it allows you to easily identify improvements ... that other teams have conveniently tested for you" — their unique bot lacked obvious features (unit refills, build order).
- "Nearly every other top team has benefited from copying the XSquare micro." Test advice: custom maps "designed to expose your problems. Save old versions of your bot and compete against them ... DO NOT upload untested bots right before important submission deadlines. At minimum, test against older versions."
- Their goal resolution became "a bunch of unorganized if-statements that was difficult to modify/debug."

### 2024 — cout for clout (Daniel Qiu, Johnny Liu, Max Wang, David Wei) — HS 1st (past.html)
- Source: https://battlecode.org/assets/files/postmortem-2024-cout-for-clout.pdf
- Copying a proven component: "it took approximately 20 minutes to steal [XSquare's] micro from his 2022 github" → rating rose from ~1850s to 1950s, then past 2000.

---

## Takeaways for an autonomous Claude-agent improvement loop

Each bullet: recommendation (inference) → evidence.

1. **Enforce the A/B gate in the harness; don't trust the agent to test.** Every candidate plays the current champion on every map, as both sides, and merges only if the win-rate gain clears a 95% confidence bound. Evidence: CodeClash — most models "deploy untested code" (Claude Sonnet 4.5 validated only 56% of rounds); 3MiceWalkIntoABar's Nudge merge gate with 95% CI, and bisecting commits lifted them from <50% to ~70% vs benchmark; JWU (2025 1st) moved from "vibes" to an all-maps A/B script; SPAARK threw 2023 and 2024 on untested last-minute changes; Ivy Zhang's turn-900 bug.
2. **Evaluate against a diverse, growing opponent pool, not just the last version.** Keep every accepted champion plus deliberately different sparring bots (rush, turtle, imitations of observed strong opponents). Evidence: food 2026 — accepting anything that beats your old bot overfits; future perfect 2016's `felix15`/`supercowpowers`/`simplesoldier18`; Baby Ducks 2021's rush-vs-econ pair; Oak 2019 lost because its assumed opponent mix was stale; Big Red 2019 reproduced Volatile's bot; High Ground 2020 couldn't tune because "we could only play against bots we had."
3. **Generate maps, including adversarial ones.** Tournament maps are built to break bots and differ from practice maps. Keep a separate held-out map set the agent never tunes on. Evidence: MFC 2021 (#1 on ladder) tuned on Sprint maps and lost on bigger finals maps; wololo/3 Musketeers 2021 low-passability ECs; stuartj 2017 immobile-archon map; future perfect 2016 zombie-heavy Sprint maps; Stone Tao 2020 devs rate maps by rush-friendliness; food 2026 — 3–5 targeted maps per behaviour, then a broad sweep.
4. **Make the agent read replays through tools, and check its claims.** Give it structured per-match summaries (who died where, when, why), not raw logs, and require every "we lost because X" to cite a specific turn/event. Evidence: CodeClash — models stop at "the first lines of a log file" and hallucinate loss causes in 17% of rounds on average, 46% on BattleSnake for Sonnet 4.5; human winners credit replay analysis (confused 2025 2nd: "extensive replay analysis"; JWU; 3 Musketeers "frame by frame ... even in wins").
5. **Build strategy-independent primitives first (by hand or tightly reviewed), then let the loop iterate on strategy.** Evidence: smite 2019, JBW 2020, High Ground 2020, MFC 2021, SPAARK 2025, food 2026 all say nav/comms/economy first; Lorem Ipsum 2026's LLM-written micro "got destroyed in almost all head to head combat scenarios" and never improved; Ivy Zhang's 30-minute greedy pathing = 100% vs old bot.
6. **Search over named knobs and discrete behaviours, not raw code.** Expose constants and behaviour toggles (a "doctrine"), let agents propose values/switches, and keep code edits for structural changes. Evidence: GP/evolution failed outright (Kraak 2024: 0 wins vs example player after 40×500; waffle 2026: one useful mutation — building traps); Metta's doctrine-of-knobs design; MFC's weighted build order and Baby Ducks' single scalar empower score were easy to tune; Gone Fishin' 2023: micro changes move win rate 30–50% vs macro ~5% — spend compute on micro.
7. **Feed the loop the meta.** When real opponents or scrims exist, have an agent write imitation bots from observed replays and add them to the pool; prioritise "copy the leader, then tweak." Evidence: McGlynn 2014–16 champion ("you don't need to invent clever strategies yourself"); High Ground's Java clone reached #1 on the ladder; GST 2026 (2nd) copied macro from teams above it; cout for clout's stolen micro +100 ELO; Kragle 2025 regretted not copying.
8. **Build pivots into the loop.** Periodically start a from-scratch or different-archetype branch and run it through the same gate, instead of only making small edits to the champion. Evidence: CodeClash — after one loss Sonnet 4.5's win chance fell below 1/3, and after five losses below 15% ("an inability ... to reconsider strategies"); JBW "if the code undergoes drastic changes ... do a bot from scratch"; NP-cgw restarted before quals and qualified unbeaten; smite rebuilt in 48 h after a rebalance.
9. **Treat the sonar as hostile and design for it.** Minimise reliance on messages; key encodings to per-game secrets (e.g. derived from map/seed); prefer deterministic shared computation (same inputs → same plan) over instructions; add spoof/replay/flood opponents to the eval pool. Evidence: Prasici 2020 replayed recorded messages and misled Steam Locomotive; JBW 2020 "didn't actually encrypt our messages that much"; smite 2019 per-map keyed encryption; 1064CBread 2016 message-flood DoS; NP-cgw 2019 poisoned the trade channel; Battlegaode 2020 minimal comms; smite/JBW/NP-cgw deterministic shared plans; wololo's gossip-gradient Bellman-Ford fits a one-value-per-turn channel.
10. **Control eval cost and codebase rot.** Run matches in CI or on a cluster, not the dev machine; stop matches early once decided; randomise/swap sides; periodically prune dead code. Evidence: 4 Musketeers 2023 moved runs to GitHub Actions because >30 maps took hours and froze a laptop; camel_case kills a match after 100 rounds of map control and runs 4 at a time; CodeClash found side advantage in Battlecode 2025 and repos that "become progressively messy and redundant"; NP-cgw's patch-on-patch bug; Musketeers' state-stack explosion.

---

## Out of scope / leads
- **Lux AI (Kaggle), created by Stone Tao after Battlecode** — reportedly a setting where RL/imitation learning did compete at the top (e.g. Season 1 / Season 2 winners). Would make a useful contrast (bigger compute, fixed rules, no bytecode). UNVERIFIED here; not researched.
- CodeClash repo/leaderboard (linked from the arXiv paper) — could be reused as a harness pattern; BattleSnake arena logs may be the closest public data to our game.
- Nudge runner https://github.com/outercloudstudio/nudge and camel_case scripts https://github.com/jmerle/battlecode-2023/tree/master/scripts — candidate designs for our eval harness.
- Producing Perfection's GitHub-Actions match runner (2022/2023) — the origin of the `run_matches.py` lineage; repo not located.
- XSquare's public micro code ("the infamous XSquare micro", copied by many top teams) — worth reading for micro structure; the lead is already covering the XSquare guide.
- Battlecode 2024 statistics (camel_case) https://jmerle.github.io/battlecode-2024-statistics/ — not read.
- No official MIT page with an LLM-use rule was found; UNSW 2026 allows AI use fully (organisers, reported by sig 2026-09-23).

