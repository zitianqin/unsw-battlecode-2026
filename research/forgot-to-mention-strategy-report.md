# forgot to mention (Team ID 264): Strategic Surge Dossier & Tactical Analysis

**Author**: Strategic Intelligence & Loop Cryptanalysis Unit  
**Target Team**: `forgot to mention` (Team ID `264`, UNSW)  
**Current Standing**: Rank 1 / 2 (Peak Elo: **2106**, Surge Baseline: **2034**)  
**Empirical Evidence Base**: **100 on-disk tournament & ranked match replays** across all 10 official maps, comprising **39 pre-surge baseline matches** (played September 30 up to 07:37 UTC) and **61 post-surge matches** (played September 30 from 07:37 UTC through 18:10 UTC).

---

## Executive Summary: The 07:37 UTC Surge

At **07:37 UTC on September 30, 2026**, Team 264 (`forgot to mention`) deployed a major bot update following a 13-minute hiatus (between Battle 680740 completed at 07:35 UTC and Battle 681807 requested at 07:48 UTC). Immediately following this deployment, Team 264 embarked on a massive win streak, surging from **2034 Elo to a peak of 2106 Elo (+72 Elo gain)** to seize the top of the ladder.

### Key Investigation Questions Answered
1. **Are their sonar protocols still the same?**  
   **YES, 100% IDENTICAL.** The cryptographic and structural analysis of over **1.57 million sonar pings** confirms that Team 264 did **not** modify their sonar protocol. The packet encoding, 99.1% omnidirectional symmetry, bitwise envelopes, collision ratios, and anchor prefixes (`0x5a1d`) remained completely unchanged.
2. **What did they change before and after?**  
   The run-up was driven by a complete overhaul of **early-game mitosis discipline, space management, and mobility economics**:
   - **Delayed First Split**: Stopped premature Round-2 blind splits (first split shifted from **Round 2.56 to Round 3.51**), ensuring initial pearls are secured before dividing into fragile length-2 bodies.
   - **Dramatic Reduction in Self-Trapping / Starvation**: `noValidAction` deaths dropped from **210.9 to 192.5 per game** (**-18.4 fewer trapped deaths per game**), reflecting vastly superior path planning in congested corridors.
   - **Aggressive Tactical Sprinting (+33.3% increase)**: Sprint rate rose from **0.39% to 0.52%**, enabling units to contest pearls and escape encirclements.
   - **Elevated Portal Traversal (+11.7% increase)**: Portal crossings rose from **108.1 to 120.7 per game**, allowing rapid map redeployment on fragmented boards.
   - **The "Cache me outside" Counter**: Their win rate against the dominant swarm leader `Cache me outside` (Team 952) jumped from **28.6% (4W–10L) to 57.1% (12W–9L)** (+28.5% win rate improvement), breaking CMO's stranglehold on the top ranks.

---

## 1. Sonar Protocol Cryptanalysis: Before vs. After 07:37 UTC

A rigorous sample of **1,577,659 sonar pings** across 100 replays reveals zero structural drift in Team 264's communication system.

### Comparative Sonar Telemetry
| Metric | Pre-07:37 UTC Baseline (n=39) | Post-07:37 UTC Surge (n=61) | Combined Dataset (n=100) | Verdict |
| :--- | :---: | :---: | :---: | :--- |
| **Total Sampled Pings** | 651,206 pings | 926,453 pings | 1,577,659 pings | Substantial empirical sample |
| **Distinct 64-bit Values** | 149,120 | 214,778 | 363,898 | Consistent entropy profile |
| **Distinctness Ratio** | **22.90%** | **23.18%** | **23.07%** | **Invariant (Δ = +0.28%)** |
| **Omnidirectional Symmetry** | **99.13%** | **99.11%** | **99.12%** | **Invariant** (Quad-directional broadcast) |
| **Bitwise OR Envelope** | `0xffffffdfffffffff` | `0xffffffdfffffffff` | `0xffffffdfffffffff` | **Identical bitmask envelope** |
| **Bitwise AND Mask** | `0x0` | `0x0` | `0x0` | **Identical bitmask mask** |
| **Primary 16-Bit Prefix** | `0x5a1d` (4,303 hits) | `0x5a1d` (7,621 hits) | `0x5a1d` (11,924 hits) | **Identical anchor prefix** |
| **Early Phase (Rounds 0–100)** | 18.0% (117,035) | 18.1% (168,005) | 18.1% | **Invariant emission curve** |
| **Mid Phase (Rounds 100–300)** | 46.1% (300,560) | 48.3% (447,146) | 47.4% | **Invariant emission curve** |
| **Late Phase (Rounds 300–500)** | 35.9% (233,611) | 33.6% (311,302) | 34.5% | **Invariant emission curve** |

### Ray Collision Surface Distribution
| Collision Target | Pre-07:37 Share | Post-07:37 Share | Behavioral Interpretation |
| :--- | :---: | :---: | :--- |
| **Kelp** | 63.1% (410,873) | 63.6% (588,794) | Primary ray termination target |
| **Ally Body** | 21.8% (142,067) | 21.2% (196,166) | Inter-swarm spatial density detection |
| **Ally Head** | 12.2% (79,160) | 12.2% (113,134) | Convoy proximity tracking |
| **Enemy Body** | 1.8% (11,926) | 1.8% (16,416) | Target acquisition surface |
| **Enemy Head** | 1.1% (7,180) | 1.3% (11,943) | Head-on collision detection |

### Reverse-Engineered Packet Specification
* **Mechanism**: Synchronized Keyed PRNG / Rolling Hash Stream.
* **Turn Transmission**: In **99.1%** of multi-ray ping turns, all four rays transmit the exact same 64-bit value simultaneously.
* **Authentication**: **None**. No HMAC, no sequence counter, no cryptographic checksum.
* **Vulnerability**: Completely susceptible to replay and coordinate injection.

---

## 2. Core Behavioral & Algorithmic Changes

While the communication protocol did not change, their movement, division, and combat heuristics underwent profound refinement:

| Behavioral Feature | Pre-07:37 Baseline | Post-07:37 Surge | Quantitative Shift | Tactical Rationale |
| :--- | :---: | :---: | :---: | :--- |
| **Match Win Rate** | **61.5%** (24W – 15D) | **77.0%** (47W – 14D) | **+15.5% Absolute Win Rate** | Dominant conversion of contested matches |
| **First Split Round** | **Round 2.56** | **Round 3.51** | **+0.95 Rounds Delayed** | Gathers pearls before dividing into weak 2-tile units |
| **Splits Per Game** | 282.1 splits | 266.5 splits | **-15.6 Fewer Splits** | Less frantic division, higher unit stability |
| **`noValidAction` Deaths** | **210.9 / game** | **192.5 / game** | **-18.4 Trapped Deaths / game** | Superior local navigation prevents corner traps |
| **Sprint Move Rate** | **0.39%** | **0.52%** | **+33.3% Relative Increase** | Active sprints to snatch high-value pearls |
| **Portal Crossings** | **108.1 / game** | **120.7 / game** | **+11.7% Higher Utilization** | Seamless transit across divided boards |
| **Head-Ons Started** | 25.2 / game | 27.0 / game | **+7.1% More Aggressive** | Decisive combat initiation |
| **Peak Living Swarm** | 40.4 dragons | 39.9 dragons | -0.5 units | Controlled population cap avoiding congestion |

### 1. The Delayed Opening Split
In the pre-surge build, dragons split as early as Round 1 or 2 (average Round 2.56). In tight spawn areas, a length-2 child immediately risked colliding with walls or enemy starting units before consuming a single pearl. In the post-07:37 build, the opening dragon prioritizes establishing clear headway and pearl ingestion, delaying mitosis to Round 3.51 on average.

### 2. Elimination of Self-Trapping (`noValidAction` Deaths)
The largest single statistical shift is the reduction of trapped deaths from **210.9 to 192.5 per game** (saving ~18 dragons every game). This indicates an improvement in their multi-agent pathing: dragons no longer blind-follow tail tiles into dead-end kelp corridors where trailing bodies choke off escape routes.

### 3. Kinetic Mobility (Sprints & Portals)
The post-07:37 bot uses sprints selectively (+33.3% increase) to beat opponents to pearl nodes and utilize portals (+11.7% increase). On portal-heavy maps like *Portals*, *Trauma*, and *Slithery Fight*, this mobility expansion directly generated match-winning leads.

---

## 3. Map Performance Matrix

The tactical modifications produced immediate performance gains on tight, maze-like, and portal-connected maps:

| Map Name | Before (W / G) | Before Win Rate | After (W / G) | After Win Rate | Performance Delta | Tactical Impact |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| **Portals** | 1 / 4 | 25.0% | 4 / 7 | **57.1%** | **+32.1%** | Massive portal traversal increase paid off |
| **Prisoners Dilemma** | 2 / 3 | 66.7% | 7 / 7 | **100.0%** | **+33.3%** | Flawless record; isolated spawns no longer punished |
| **Trauma** | 2 / 3 | 66.7% | 4 / 4 | **100.0%** | **+33.3%** | Corridor navigation fixes prevented choke deaths |
| **Slithery Fight** | 2 / 5 | 40.0% | 5 / 7 | **71.4%** | **+31.4%** | Drastic reduction in winding lane entrapment |
| **Schooltime** | 3 / 6 | 50.0% | 6 / 8 | **75.0%** | **+25.0%** | Pearl racing efficiency significantly improved |
| **Default** | 2 / 4 | 50.0% | 4 / 6 | **66.7%** | **+16.7%** | Open-field pearl harvesting streamlined |
| **Devil** | 3 / 3 | 100.0% | 4 / 4 | **100.0%** | +0.0% | Retained 100% win rate |
| **Trophy** | 2 / 4 | 50.0% | 3 / 6 | 50.0% | +0.0% | Neutral |
| **Queen Of Spades** | 3 / 3 | 100.0% | 5 / 6 | 83.3% | -16.7% | Minor variance (single loss) |
| **Autarky** | 4 / 4 | 100.0% | 5 / 6 | 83.3% | -16.7% | Minor variance (single loss) |

---

## 4. Head-to-Head Opponent Breakdown

The primary engine of Team 264's climb to Rank 1 was turning their losing record against top teams into decisive winning records:

```
Cache me outside (ID 952):
  Pre-07:37:  4W - 10L ( 28.6% Win Rate) [█████░░░░░░░░░░░░░░░]
  Post-07:37: 12W -  9L ( 57.1% Win Rate) [███████████░░░░░░░░░]  <-- +28.5% SURGE

Cutlery (ID 306):
  Pre-07:37:  8W -  2L ( 80.0% Win Rate) [████████████████░░░░]
  Post-07:37:  8W -  2L ( 80.0% Win Rate) [████████████████░░░░]  <-- Maintained dominance

SSS (ID 91):
  Pre-07:37: 10W -  0L (100.0% Win Rate) [████████████████████]
  Post-07:37: 14W -  1L ( 93.3% Win Rate) [██████████████████░░]  <-- Total hegemony

Tier-1 Contenders Defeated Post-07:37:
  Sponge(Albert and Bob) (ID 213):  4W - 1L (80.0%)
  WeHaveQuizzes          (ID  87):  4W - 1L (80.0%)
  free trip to sydney pls (ID 368):  5W - 0L (100.0%)
```

### Why the Changes Neutralized Cache me outside
Prior to 07:37 UTC, `Cache me outside` overwhelmed Team 264 by blanketing the map with 35+ length-2 dragons, baiting Team 264's early length-2 units into starved corridors and cutting off their pearl access.  
By delaying their initial division by nearly a full round, Team 264 enters the early-mid game with thicker, higher-energy units that survive initial collisions and sprint into uncontested quadrants via portals, turning a 28.6% underdog matchup into a 57.1% winning edge.

---

## 5. Exploitation & Countermeasure Strategy

Because Team 264's sonar protocol is **completely unchanged**, all previously cataloged eavesdropping and spoofing vectors remain fully active:

1. **Acoustic Decoy & Ray Sniffing**:
   - Because 99.1% of their pings fire identically in all four directions, intercepting any single ping immediately reveals the emitting dragon's team identity and presence.
2. **Zero MAC / Replay Forgery**:
   - Packets contain no authentication or timestamping.
   - Forged coordinates broadcast near portal exits or dead-end kelp clusters can disrupt their convoy following routines, baiting their subordinate units into congested areas.
3. **Exploiting the Delayed Split**:
   - In Rounds 0–3, Team 264 now delays splitting. Fast aggressive rush bots (e.g. SSS or heavy sprint combat builds) have a 1–2 round window where Team 264 only has 1–2 active heads on the board rather than 4. Early-game sprint cutoffs on maps like *Trophy* or *Devil* can decapitate their fleet before mitosis begins.

---

## Conclusion

Team 264's rating run-up from 7:37 UTC was achieved strictly through **algorithmic and tactical refinements in bot logic** (delayed opening mitosis, superior dead-end collision avoidance, +33% sprint activation, and +12% portal usage), while their **sonar protocol remained completely unchanged**.
