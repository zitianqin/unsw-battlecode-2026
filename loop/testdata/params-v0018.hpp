#pragma once

// Every tunable number the bot uses, with its default. Later experiments change one field at a time,
// so keep each one named for what it controls and read it through `P` (below), never a literal.
struct Params {
    // --- Split policy -------------------------------------------------------------------------
    int splitAtLength = 4;       // split once the dragon is at least this long
    int childLength = 2;         // segments given to the child
    int splitEnemyHeadRadius = 1; // no split while an enemy head is this close (Manhattan) to our head
    int splitParentSpace = 12;   // the parent's best move must be open and physically reach this many tiles ...
    int splitParentTerritory = 6; // ... of which it must reach this many before every other head
    int splitChildSpace = 8;     // the child's head needs an exit whose flood fill reaches this many tiles

    // --- Survival -----------------------------------------------------------------------------
    int spaceMargin = 2;         // a move needs length + margin reachable tiles
    int spaceCap = 40;           // the flood fill stops counting here ...
    int spaceCapPerLength = 2;   // ... or at this many tiles per segment, if more
    int visibleNeedCap = 10;     // the pessimistic (visible tiles only) fill wants min(need, this) tiles
    int otherTailMargin = 1;     // extra steps before another dragon's tail tile counts as free (it may eat)
    bool blockUntrackedOwn = true; // our own segment off the tracked body stays blocked until the latest it could free
    bool numberOwnTail = true;   // a fresh child numbers its own segments back from its visible tail
    bool floodTerritoryOnly = true; // the soft space count only counts tiles we reach before every other head

    // --- Head-on trades -------------------------------------------------------------------------
    int minUnitsToTrade = 3;     // never trade a dragon when the team has fewer than this many
    int riskTradeMinGain = 0;    // standing next to an enemy head is fine when theirLen - ourLen >= this (unless outnumbered, below)
    int attackMinGain = 0;       // ramming an enemy head needs theirLen - ourLen >= this
    int evenTradeMinEdge = 0;    // a ram that gains no length needs visible friendly heads - enemy heads >= this
    bool outnumberedRamGuard = true; // while visible friendly heads - enemy heads <= ramGuardMaxEdge, a tile an enemy
    int ramGuardMaxEdge = -1;        // ... head at least as long (visibleLen >= ours) can step onto next is risky too
    int maxRamSteps = 3;         // sprint up to this many steps to ram an enemy head (1 = no sprints)
    int protectMinLength = 6;    // from P.mergeRound our longest-looking dragon (this long) refuses all trades
    bool closeInToRam = true;    // a short dragon walks towards a longer enemy head until findRam can reach it (hunt.hpp)
    int hunterMaxLength = 5;     // ... only dragons at most this long hunt
    int huntMaxDist = 6;         // ... at most this many free steps away (and beyond our sprint reach)
    int huntMinEdge = 0;         // ... and only while visible friendly heads - enemy heads >= this

    // --- Merge cascade (feed.hpp) ------------------------------------------------------------------
    // From mergeRound the swarm folds its mass into one long dragon for the round-500 tiebreak: no ordinary
    // splits (split.hpp), the longest-looking dragon becomes a protected king (safety.hpp) that approaches
    // teammates that would merge into it, and any dragon dies beside a teammate at least as long so it eats
    // our pearls. Pairwise merges chain into a tree that ends in one king. A folded team is easy prey for a
    // swarm that never folds (its short dragons ram our long ones, and it outnumbers us), so the fold starts
    // late: from 300 it was wiped out in a third of the games against one.
    int mergeRound = 450;
    int mergeMinUnits = 8;       // merging stops once the team is down to this many dragons
    int mergeRadius = 2;         // the teammate must reach our head tile within this many steps, before any other head
    bool kingSeeksFeeders = true; // a king with no pearl close by targets the nearest teammate that would feed it
    int kingPearlReach = 4;      // ... where close means eaten within this many steps

    // --- King beacon and final fold (fold.hpp) ------------------------------------------------------
    // The merge cascade only folds dragons that meet; long dragons far apart end the game separate, and only
    // the longest counts. A dragon at least as long as every teammate it knows of reports its head and length
    // over sonar (relayed like the belief map), and late in the game every dragon walks to it and merges, as the
    // ladder's leaders fold between r400 and r450 and grow one king for the last 50-100 rounds.
    bool kingBeacon = true;      // the king sends (and everyone relays) a king report
    int beaconRound = 410;       // ... from this round
    int kingMinLength = 4;       // ... when at least this long
    int kingReportAge = 10;      // a king report older than this many rounds is ignored
    int gossipKingValue = 1000;  // a fresh king report's gossip value: ahead of every belief report
    bool finalFold = true;       // dragons walk to the reported king and merge into it
    int foldRound = 415;         // ... from this round
    int foldMinLength = 1;       // ... when at least this long (1: every dragon)
    bool kingHoldsSplits = true; // ... and from foldRound the king stops ordinary splits (folders feed it)
    int foldMergeRadius = 4;     // a folding dragon merges when the king reaches our head within this many steps
    bool foldMergesOnlyKing = true;   // a folding dragon merges early and wide only into the king, not into any
                                      // longer teammate on the way (each extra merge drops half the mass again)
    bool foldIgnoresMinUnits = false; // a folding dragon merges even when the team is down to P.mergeMinUnits
    bool foldIgnoresEnemy = false;    // a folding dragon merges with an enemy head in view

    // --- Pearls and exploring -------------------------------------------------------------------
    int maxTargetDist = 14;      // ignore pearls further than this (path length)
    int maxTargetCandidates = 24; // pearls/spawn tiles examined per turn, nearest first
    int pearlMemoryRounds = 40;  // trust an out-of-sight pearl for this many rounds
    int spawnSlack = 2;          // aim for a spawn tile only if it fires at most this many steps after we arrive
    int probablePenalty = 4;     // extra cost (in steps) of a pearl we only infer from a countdown
    int rememberedPenalty = 2;   // extra cost (in steps) of a pearl we saw earlier but cannot see now
    bool yieldToEnemyCloser = true; // skip a pearl an enemy head reaches strictly sooner
    int exploreStride = 4;       // explore targets are sampled on a grid this coarse
    int exploreRetarget = 25;    // pick a fresh explore target at least this often (rounds)
    int staleCap = 60;           // staleness is capped here; never-seen tiles count as this
    int exploreStaleWeight = 3;
    int exploreDistWeight = 2;
    int exploreFriendWeight = 2; // prefer explore targets far from visible teammates
    int exploreFriendCap = 12;

    // --- Team belief map over sonar (belief.hpp, gossip.hpp) -------------------------------------
    bool teamGossip = true;      // send our belief map's fresh reports (and relays) over sonar
    bool beliefExplore = true;   // explore staleness counts tiles any teammate reported, and a target a
                                 // teammate reported after we chose it is dropped
    bool beliefPearls = true;    // pearl targets use teammates' pearl sightings and countdowns
    int sharedPearlPenalty = 4;  // extra cost (in steps) of a pearl only a teammate reported
    int gossipMaxAge = 30;       // never send a report older than this many rounds
    int gossipGainCap = 20;      // a report's value: rounds fresher than what we last sent of it, capped here ...
    int gossipPearlValue = 5;    // ... plus this per pearl it announces
    int gossipCountdownMax = 20; // share a countdown only when its spawn is at most this many rounds away
    double enemyPrior = 0.1;     // chance of an enemy head in a block nobody has reported on
    double enemyHalfLife = 8;    // rounds for a report's enemy evidence to fade halfway to the prior

    // --- Fast tiles and dead-end farms ------------------------------------------------------------
    int fastMinResets = 2;       // a tile is fast once we have watched it reset this many times ...
    int fastMaxGap = 2;          // ... to a mean countdown of at most this many rounds
    int fastMaxResets = 20;      // stop recording resets after this many (keeps old evidence bounded)
    int fastTilePenalty = 1;     // extra cost (in steps) of an out-of-sight fast tile: it is probably full
    int farmClusterRadius = 2;   // a fast tile's yield counts the fast tiles within this Chebyshev radius ...
    double farmClusterWeight = 0.5; // ... each dividing its cost by 1 + this per extra fast tile
    int farmMinPearls = 2;       // a closed pocket is worth entering with this many pearls/fast tiles inside ...
    int farmMinEndLength = 4;    // ... if our length plus those pearls reaches this, so we can split out

    // --- Move scores (bigger is better) --------------------------------------------------------
    double lethalScore = -1e12;       // walls, bodies, our own tail
    double friendHeadOnScore = -2e12; // kills two of ours: worse than a wall, even when trapped
    double badTradeScore = -1e10;     // ramming an enemy head when the trade is bad
    double doomedScore = -1e9;        // a best move scoring at most this kills us: split the tail out
    double unknownPortalPenalty = 5e5;
    double portalCrossPenalty = 30;   // price of stepping blind through the unknown portal we explore towards
    double shortfallPerTile = 1e6;    // per tile the pocket is short of what we need
    // Per tile the visible-only fill is short when the pocket's exits are all out of sight. At most
    // visibleNeedCap - 1 tiles short, so the total stays below one step of explore or pearl-target distance: it
    // never turns a dragon off its target, only orders moves equally close to it (against space and teammate
    // spacing). A dead end in view is refused by shortfallPerTile. It still clears MoveEval::safe for hunt's close-in.
    double unseenShortfallPerTile = 5;
    double riskPenalty = 3e6;         // an enemy head could ram us and the trade is bad for us
    double attackBonus = 2000;
    double attackPerLength = 200;
    double pearlBonus = 150;          // stepping onto a pearl
    double targetWeight = 100;        // per step of distance to the pearl target
    double exploreWeight = 60;        // per step of distance to the explore target
    double earlySpawnPenalty = 300;   // standing on a spawn tile before it fires blocks the spawn
    double spaceWeight = 3;           // per reachable tile, up to spaceCap
    double friendRepelWeight = 25;    // per unit of closeness to a teammate head
    int friendRepelRadius = 3;

    bool debugIndicator = false;      // label dragons in replays (costs output bytes; keep off in the ladder)
};

inline constexpr Params P{};
