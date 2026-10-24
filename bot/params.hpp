#pragma once

// Every tunable number the bot uses, with its default. Later experiments change one field at a time,
// so keep each one named for what it controls and read it through `P` (below), never a literal.
struct Params {
    // --- Split policy -------------------------------------------------------------------------
    int splitAtLength = 4;       // split once the dragon is at least this long
    int childLength = 2;         // segments given to the child
    int splitEnemyHeadRadius = 1; // no split while an enemy head is this close (Manhattan) to our head
    // The split's room gates (0 = off: split whenever long enough, as the out-breeding leaders do). The parent's
    // best move is always open and reaches length - childLength + spaceMargin tiles with our body standing.
    int splitParentSpace = 0;    // the parent's best move must be open and physically reach this many tiles ...
    int splitParentTerritory = 0; // ... of which it must reach this many before every other head
    bool splitTerritoryEnemyOnly = true; // ... counting only enemy heads: teammates' heads take no share of it
    int splitChildSpace = 0;     // the child's head needs an exit whose flood fill reaches this many tiles
    // An ordinary split's child exit must be a tile this dragon has had in view (World::lastSeen): a tile never seen
    // reads empty (World::observe), and a newborn child's tail touches its parent's body, out of its view.
    bool splitExitMustBeSeen = true;
    // Cache me outside's split tempo (I1199): it splits at P.splitAtLength on 91% of its splits (ours 57%, 68 online
    // games between us). Off, the parent's space gates (shouldSplit's room and fill: its best move open and reaching
    // length - childLength + spaceMargin tiles with its body standing) don't hold a dragon at exactly splitAtLength;
    // a longer one keeps them (off at every length, our r396 king fell 4.1 in 36 local games) ...
    bool splitSpaceGates = false;
    bool splitUntrackedTail = true; // ... and, with them off, one at splitAtLength splits with its tail out of sight too
    // Shedding under threat (split.hpp's shedLength): a long dragon a shorter enemy head can sprint-ram keeps a
    // length-2 front and splits the rest off; from the fixed fold (state.hpp's threatShedEnd) only as split.hpp's
    // threatShedOn allows (with longShedsInFold on, at every round):
    bool shedUnderThreat = true;
    bool kingShedsInFold = true;  // ... from threatShedEnd a dragon that believes it is king (fold.hpp's isKing) still sheds
    bool longShedsInFold = true;  // ... from threatShedEnd every dragon still sheds (kingShedsInFold then decides nothing)
    int shedMinLength = 6;       // ... only dragons at least this long shed (without shedOnValue)
    // With shedOnValue (I468) the length a threatened dragon needs is split.hpp's shedPays instead: the ram's expected
    // cost that the shed saves, shedRamChance x ramWGain (the ram model's swing per segment, rammodel.hpp) per segment
    // past the length-2 front, against the shed's, shedChildFail per segment of the child.
    bool shedOnValue = true;
    double shedRamChance = 0.49;  // chance a threatened, unshed dragon dies head-on within 3 rounds (E0477: 4-5-long
                                  // ones in champion-played games, 7,274 turns; 2% unthreatened)
    double shedChildFail = 0.0126; // chance a shed child dies on its own move in its first 3 rounds (3 x 0.42% a turn, I468)
    int shedThreatSlack = 1;     // ... the threat reaches our head within its sprint reach plus this many free steps
    bool shedNeedsTail = false;  // ... only with our tail in view (false: a tail out of sight sheds unchecked)
    int shedReserve = 0;         // ... ordinary splits leave at least this many unit slots free on every map (rescueReserve)
    // Heading into a closed pocket with our tail trapped in another one, split our rear off (shed.hpp):
    bool shedKnownTail = true;   // ... a tail we track is trapped when no exit of it reaches trappedTailSpace tiles
    int trappedTailSpace = 8;    //     past our body (in crowds, other dragons' bodies can close it off too)
    bool shedAtBirth = true;     // ... a tail out of sight is trapped on the second turn of a dragon born long
    bool shedStarterAnyTurn = true; // ... and on any turn of a starting dragon (no parent beside its tail) until it sheds
    bool shedEnemyByPath = false;   // ... its enemy-head check (P.splitEnemyHeadRadius) counts steps through known edges
    // An ordinary split or trapped-tail shed whose child would start in a room it knows no way out of splits only
    // when the room's free tiles hold both halves for the rounds left (roomsplit.hpp):
    bool closedRoomSplits = true;
    // Rescue slots (state.hpp's rescueReserve): on a map of at least reserveMinArea tiles, from rescueFrom until
    // P.mergeRound, an ordinary split leaves the team's last rescueSlots unit slots free, so a long dragon trapped
    // at the cap can still split its tail out (splitOutLength) or shed it (shed.hpp). Split-outs and sheds use
    // every slot. Smaller maps rarely hold the cap and end in eliminations, where every dragon counts.
    bool reserveRescueSlots = true;
    int rescueFrom = 0;
    int rescueSlots = 2;
    int reserveMinArea = 1152;   // ... W * H tiles; 0 = every map
    bool farmHonorsReserve = true; // ... and farm entry refuses a closed pocket once only the reserve is left

    // --- Survival -----------------------------------------------------------------------------
    int spaceMargin = 2;         // a move needs length + margin reachable tiles
    // With roomByRoundsLeft (I496, I398) a survival fill never asks for more than the tiles the moves left in the game
    // can use (safety.hpp's holdCap): a pocket that holds the dragon to the last round is open.
    bool roomByRoundsLeft = true;
    int spaceCap = 40;           // the flood fill stops counting here ...
    int spaceCapPerLength = 2;   // ... or at this many tiles per segment, if more
    int visibleNeedCap = 10;     // the pessimistic (visible tiles only) fill wants min(need, this) tiles
    int otherTailMargin = 1;     // extra steps before another dragon's tail tile counts as free (it may eat)
    bool blockUntrackedOwn = true; // our own segment off the tracked body stays blocked until the latest it could free
    bool numberOwnTail = true;   // a fresh child numbers its own segments back from its visible tail
    bool floodTerritoryOnly = true; // the soft space count only counts tiles we reach before every other head

    // --- Head-on trades -------------------------------------------------------------------------
    int minUnitsToTrade = 3;     // never trade a dragon when the team has fewer than this many
    int riskTradeMinGain = 0;    // pricedGuard off: standing next to an enemy head is fine when theirLen - ourLen >= this
                                 // (unless outnumbered, below)
    int attackMinGain = 0;       // ramming an enemy head needs theirLen - ourLen >= this
    int evenTradeMinEdge = 0;    // a ram that gains no length needs visible friendly heads - enemy heads >= this
    int outnumberedAttackMinGain = 3; // while visible friendly heads - enemy heads < 0, a ram needs theirLen - ourLen >= this
    // The learned ram gate (rammodel.hpp): ram when the expected swing (a linear model fit on our own rams, E0204) is
    // above ramModelMin, instead of the three rules above. The weights are the model's coefficients.
    bool ramModel = true;
    double ramModelMin = 0;      // expected swing a ram needs: declining swings nothing
    int ramModelMinGain = -3;    // the model's range, the rams it was fit on: theirLen - ourLen >= this
    double ramWBias = 0.139;
    double ramWGain = 0.987;         // per segment the victim is longer than us (visible)
    double ramWEdge = 0.497;         // per unit of Safety::headEdge
    double ramWNearFriends = 0.624;  // per other friendly head within dropEdgeRadius of the victim
    double ramWNearEnemies = -0.691; // per other enemy head within dropEdgeRadius of the victim
    // Who eats an even ram's drop (harvest.hpp): a ram on a head as long as us (visible) needs the drop tiles our other
    // heads reach first, less those enemy heads reach first, to be at least evenTradeMinHarvest, instead of the head
    // counts of the model or the hand rule above (rammodel.hpp's ramWorth).
    bool evenTradeHarvest = true;
    int evenTradeMinHarvest = 1;
    int harvestReach = 8;             // steps a head may take to a drop tile and still count
    // The ram guard priced as our rams are (Safety::badRammer): a tile an enemy head can step onto next is risky when
    // its ram on us there is worth starting by our own ram gate (the ram model, or dropHarvestEdge for an even head),
    // seen from its side, instead of the hand rule of riskTradeMinGain and the outnumbered guard below.
    bool pricedGuard = true;
    bool outnumberedRamGuard = true; // pricedGuard off: while visible friendly heads - enemy heads <= ramGuardMaxEdge,
    int ramGuardMaxEdge = -1;        // ... a tile an enemy head at least as long (visibleLen >= ours) can step onto next
                                     // is risky too
    bool fullEnemyReach = true;  // price enemy sprints by length and pearls in escape and shed checks (enemy_reach.hpp)
    bool shedUnseenTailReach = true; // ... and the shed gives a head whose tail is out of sight at least P.maxRamSteps
    int maxRamSteps = 3;         // an enemy's least reach with its tail out of sight (enemyReach), the legacy enemy cap
                                 // (fullEnemyReach off) and our legacy ram cap (1 = no sprints)
    bool longSprintRams = true;  // extend our rams and close-in reach, independently of the enemy reach model
    int ownRamSteps = 6;         // our extended sprint cap; extra reach must be paid by our starting length
    bool escapeSprint = true;    // when every non-lethal step is in a bad rammer's one-step reach, sprint 2 steps to a
    int escapeMinLength = 4;     // ... tile beyond every bad rammer's reach (escape.hpp), for dragons at least this long
    int protectMinLength = 6;    // from P.mergeRound our longest-looking dragon (this long) refuses all trades
    bool closeInToRam = true;    // a short dragon walks towards a longer enemy head until findRam can reach it (hunt.hpp;
                                 // longer prey is the hand rule's, Safety::attackGood)
    int hunterMaxLength = 5;     // ... only dragons at most this long hunt
    int huntMaxDist = 6;         // ... at most this many free steps away (and beyond our sprint reach)
    int huntMinEdge = 0;         // ... and only while visible friendly heads - enemy heads >= this
    bool huntEqualPrey = true;   // ... and also walks towards an enemy head of our visible length when findRam's gate
                                 // (ramWorth) would ram it from here (hunt.hpp); longer prey still comes first

    // --- Merge cascade (feed.hpp) ------------------------------------------------------------------
    // From mergeRound the swarm folds its mass into one long dragon for the round-500 tiebreak: no ordinary
    // splits (split.hpp), the longest-looking dragon becomes a protected king (safety.hpp) that approaches
    // teammates that would merge into it, and any dragon dies beside a teammate at least as long so it eats
    // our pearls. Pairwise merges chain into a tree that ends in one king. A folded team is easy prey for a
    // swarm that never folds (its short dragons ram our long ones, and it outnumbers us), so the fold starts
    // late: from 300 it was wiped out in a third of the games against one. Set to the last round, as the
    // out-breeding `breeder` plays, the cascade lasts that one round: until then dragons keep splitting, only the
    // final fold (foldRound) merges, into the king, and the king is unprotected (it still seeks feeders from the
    // fold's start with kingSeeksFeedersInFold).
    int mergeRound = 499;
    int mergeMinUnits = 8;       // merging stops once the team is down to this many dragons
    int mergeRadius = 2;         // the teammate must reach our head tile within this many steps, before any other head
    bool kingSeeksFeeders = true; // a king with no pearl close by targets the nearest teammate that would feed it
    int kingPearlReach = 4;      // ... where close means eaten within this many steps
    bool kingSeeksFeedersInFold = true; // ... and from the fold's start (state.hpp's foldStart), a dragon that believes it is king
    bool foldSeekerHeardLongest = true; // ... only while at least as long as every teammate heard of this game (Belief::longestHeard)

    // --- King beacon and final fold (fold.hpp) ------------------------------------------------------
    // The merge cascade only folds dragons that meet; long dragons far apart end the game separate, and only
    // the longest counts. A dragon at least as long as every teammate it knows of reports its head and length
    // over sonar (relayed like the belief map). From the fold's start (state.hpp's foldStart) dragons within foldPathCap
    // walk to it and merge.
    bool kingBeacon = true;      // the king sends (and everyone relays) a king report
    int beaconRound = 405;       // ... from this round (earlier while fold.hpp's kingSeedOn, or with modeModel's fold)
    int kingMinLength = 4;       // ... when at least this long
    int kingReportAge = 12;      // a king report older than this many rounds is ignored
    bool reportByDrift = true;   // ... but a folder trusts one while its age is at most driftFactor x its path to the
                                 // reported head (fold.hpp's foldKing); isKing, relays and the heir window keep kingReportAge
    double driftFactor = 1.0;    // coefficient of that window: rounds of age a path step buys (a head moves a tile a round)
    int gossipKingValue = 1000;  // a fresh king report's gossip value: ahead of every belief report
    bool kingSuccession = true;  // a king that splits out passes the crown to its child: the child ignores its
                                 // parent's reports and reports itself as heir, and teammates drop the parent's
    bool finalFold = true;       // dragons walk to the reported king and merge into it
    int foldRound = 397;         // ... from this round, but not before beaconRound (state.hpp's foldStart)
    // Learned fold start (I485, fold.hpp's startFold): with modeModel the final fold starts when the rounds left fall to
    // the fold window (fold.hpp's foldWindowFor), and a king beacons from then when that is before beaconRound;
    // off, foldRound and beaconRound decide as above. The window, fitted on game results (E0345):
    bool modeModel = true;
    double foldWindow = 95;            // ... rounds before MAX_ROUNDS (the fixed start's, max(foldRound, beaconRound)), plus
    double foldWindowPerKTileSq = 6.59; // ... this many times the square of the map's size in 1000s of tiles (W * H),
    double foldWindowMin = 50;         // ... within the windows the fit played
    double foldWindowMax = 200;
    int foldMinLength = 1;       // ... when at least this long (1: every dragon)
    bool partialFold = true;     // ... and has time for the king to eat part of its drop (foldMinFeedRounds), not all
    int foldMinFeedRounds = 10;  // ... at least this many rounds of eating after it arrives (a shorter dragon: its length)
    // Fold near, breed far: on a map of at least localFoldArea tiles only a dragon within foldReach path steps of
    // the king's head folds; distant non-kings keep eating, farming and splitting when a feeder frees a slot,
    // until mergeRound starts the cascade. Smaller maps fold whoever has time.
    bool localFold = true;
    int foldReach = 12;
    int localFoldArea = 2400;    // ... W * H tiles
    bool kingHoldsSplits = true; // ... and from the fold's start the king stops ordinary splits (folders feed it)
    bool kingFoldSkipsFarms = true; // ... and farm entries (each ends in a split-out), on every map (fold.hpp's kingSkipsFarms)
    // Seeding the king (fold.hpp's kingSeedOn): on a map of at least kingSeedArea tiles (on a smaller one with
    // kingSeedStall, while the team's count has stalled) the king reports itself before beaconRound and makes no
    // ordinary splits or farm trips while a split child is worth less to it than the childLength it takes (a king-line
    // child returned about 1 length by r499 in every 100-round band, 71 games):
    bool kingSeedOnValue = true;
    int kingSeedArea = 1152;     // ... W * H tiles; smaller maps end in eliminations, where every dragon counts
    int kingHoldLead = 2;        // ... with the team this many slots or fewer below where ordinary splits stop (another
                                 //     dragon's split refilled a freed slot there in a median 1 round) ...
    int kingSeedRound = 300;     // ... and from this round, whatever the team's size
    bool kingSeedStall = true;   // on a map under kingSeedArea, seed while the team's count has stalled and it outlasts
                                 //     the game at its measured rate (fold.hpp's unitStall; I1159, I1257)
    int foldMergeRadius = 4;     // a folding dragon merges when the king reaches our head within this many steps
    bool foldMergesOnlyKing = true;   // a folding dragon merges early and wide only into the king, not into any
                                      // longer teammate on the way (each extra merge drops half the mass again)
    bool foldIgnoresMinUnits = false; // a folding dragon merges even when the team is down to P.mergeMinUnits
    // A folding dragon also merges past P.mergeMinUnits (feed.hpp's foldPastFloor) when the rest of the team could not
    // be eliminated in the rounds left even at wipeSafety times the fastest measured wipe-out rate: unitCount - 1 >
    // wipeRate * (wipeRefArea / (W * H))^wipeAreaPower * rounds left * wipeSafety.
    bool floorByTime = true;
    // Fitted (E0285) on 95 eliminations of our code, 10 online: the dragons lost a round from the round the team fell
    // to mergeMinUnits to its elimination, log-linear in area; wipeRate is the fastest, folded teams wiped in the fold
    // window included.
    double wipeRate = 0.27;      // ... the fastest wipe-out, in dragons a round, on wipeRefArea tiles
    int wipeRefArea = 1024;      // ... the map area wipeRate is scaled to (unit of the formula)
    double wipeAreaPower = 0.33; // ... how the rate falls with area: (wipeRefArea / (W * H))^wipeAreaPower
    double wipeSafety = 2;       // ... the margin over the measured rate
    bool foldIgnoresEnemy = false;    // a folding dragon merges with an enemy head in view
    bool foldFeedByDrop = true;       // ... or, with one in view, when our heads win its drop race (feed.hpp)

    // --- Feeding the king (capfeed.hpp) ------------------------------------------------------------
    // A short dragon beside the king dies into kelp or its own body when the king eating its drop now beats what it
    // would bring the king alive through the final fold: capFeedShare * ceil(L/2) >=
    // capFeedFoldShare * ceil((L + g * rounds to the fold) / 2) + unit cost + capFeedMargin, g its own pearls a round.
    // At the ceiling where ordinary splits stop the unit costs nothing (the next blocked split refills the slot).
    bool capFeed = true;
    int capFeedKingRatio = 3;         // ... into the longest teammate we know of, at least this many times our length
    double capFeedShare = 0.88;       // share of a feed's drop its eater takes (399 feeds, 24 games on 1152+ tiles)
    double capFeedFoldShare = 0.425;  // share of a folding dragon's drop the king eats (rule counts of 307dd8c, n 1,702)
    double capFeedMargin = 0;         // length a feed must win by
    int intakeWindow = 20;            // g: our pearls a round over this many turns ...
    double capFeedTeamGrowth = 0.045; // ... the missing turns of a younger dragon counting at the mean g of dragons
                                      //     beside the king at the ceiling (n 1,548, 72 games)
    // With intakeByLife (I991), g is instead every pearl we ate since birth plus intakePriorTurns turns at
    // capFeedTeamGrowth, over the turns we have lived plus intakePriorTurns: our own record shrunk to the team mean.
    bool intakeByLife = true;
    double intakePriorTurns = 40;     // ... the prior's weight in turns (a coefficient: fitted by held-out error of the
                                      //     pearls eaten to the fold, E0478, 42,087 capfeed and room-hold turns of 74
                                      //     games; 40 in every official-map-left-out fold and on worldgen held out)
    // Below the ceiling too (I697, I826) while the king seed runs (fold.hpp's kingSeedOn), where a death leaves its slot
    // empty, priced on the alive side (capfeed.hpp's unitCost):
    bool capFeedBelowCap = true;
    bool unitCostByRefill = true;     // ... the unit's intake over the rounds the team's count takes to regain its level
                                      //     (1 / its net unit growth a round), not E0273's fitted value below
    double capFeedUnitValue = 0.0106; // ... without it, king length a unit's descendants bring a round its slot stays
                                      //     empty (E0273's fit through 0 on 390 below-ceiling dragons beside the king
                                      //     from r250, 44 games: 0.0121 on generated maps, 0.0016 on worldgen and official)
    bool capFeedApproach = true;      // a feeder that passes every gate but the distance (the king in view or reported,
                                      //     no enemy head in view) walks to the king's head, if it gets there before the fold
    bool capFeedByRace = true;        // an enemy head in view blocks a feed only when it wins our drop's race
                                      //     (harvest.hpp's feedDropEdge), not always
    bool capFeedInPlace = true;       // a feeder with no kelp or own-body step dies where it stands by an illegal split

    // --- Pearls and exploring -------------------------------------------------------------------
    int maxTargetDist = 56;      // ignore pearls further than this (path length); with rateReach, the reach of a
                                 // dragon whose exploring finds pearls at the mean rate (Targets::reachSteps)
    // With rateReach, a pearl's reach is maxTargetDist scaled by this dragon's steps per pearl found by exploring over
    // their mean (Targets::reachSteps): far pearls pay where exploring feeds us slowly, near ones where it's quick.
    bool rateReach = true;
    double reachPriorSteps = 14.7; // coefficient: mean steps per pearl found by exploring (45,125 episodes, 134 games of
                                   // v0061's logging copy on eval, worldgen and official maps)
    double reachPriorWeight = 4;   // coefficient: episodes that mean counts as, fitted on the same rows (held-out
                                   // deviance below a constant on official, worldgen and eval maps alike)
    int maxTargetCandidates = 24; // pearls/spawn tiles examined per turn, cheapest first
    int pearlMemoryRounds = 40;  // trust an out-of-sight pearl for this many rounds
    int spawnSlack = 2;          // aim for a spawn tile only if it fires at most this many steps after we arrive
                                 // (with rateSpawnSlack off)
    // With rateSpawnSlack the slack is this dragon's own steps per pearl found by exploring (Targets::spawnSlackSteps):
    // wait for a known spawn whenever waiting is no slower than exploring.
    bool rateSpawnSlack = true;
    int probablePenalty = 4;     // extra cost (in steps) of a pearl we only infer from a countdown
    int rememberedPenalty = 2;   // extra cost (in steps) of a pearl we saw earlier but cannot see now
    bool yieldToEnemyCloser = true; // skip a pearl an enemy head reaches strictly sooner
    // Harvesting drops (I747, ported from the gauntlet `harvester` entry), four switches, one behaviour each:
    // harvestDrops: a visible pearl a death dropped (World::dropNow) is contested, never yielded to a head that gets
    //   there first, and its cost is divided by the drop pearls in view within dropEdgeRadius of it (itself
    //   included), so heads leave other targets for a fight's drop (targets.hpp);
    // harvestFights: an enemy head with one of our other heads within dropEdgeRadius is a target worth its drop
    //   (Targets::addFights), so heads wait at the fight's edge (the ram guard keeps them off the tiles beside it);
    // harvestStep: a drop pearl beside no enemy head longer than us is stepped onto despite the ram guard
    //   (scoring.hpp);
    // harvestRamGate: every ram needs our other heads to reach more of its drop first (dropHarvestEdgeFor at least
    //   evenTradeMinHarvest, rammodel.hpp), not only even ones. Off: in E0258's shadow games against the swarm it cut
    //   the rams we start and our pearls with them (the losing avoidance pattern), while the drop contest carried the
    //   drop-share gain; a follow-up tests it alone.
    bool harvestDrops = true;
    bool harvestFights = true;
    bool harvestStep = true;
    bool harvestRamGate = false;
    bool pearlKeepHeading = true; // equally cheap pearl/spawn targets go to the one most nearly along our facing
    // With our swarm full (the unit cap less rescueReserve), its bodies wall a long dragon in and a split out of a
    // pocket costs its front or finds no slot: it prefers pearl and explore targets in open ground (Targets::crowdAt).
    bool crowdTargets = true;    // with the swarm full, a dragon at least roomMinLength long ...
    int roomMinLength = 8;
    // ... or with crowdByLength (I473) a dragon of any length, the price per tile scaled by length / crowdRefLength
    // (the length at which a tile costs crowdWeight steps): what teammates walling it in cost grows with its length
    bool crowdByLength = false;
    double crowdRefLength = 8;
    double crowdWeight = 1;      // ... adds this many steps to a target's cost per teammate body tile around it:
    int crowdRadius = 3;         //     within this Chebyshev radius of the target, ...
    int crowdMemoryRounds = 5;   //     seen there within this many rounds (by us: sonar carries no bodies)
    int crowdFreeDist = 2;       // ... except a pearl at most this far from our head (straight-line; path for a farm)
    // A pearl target whose approach the move scorer refuses (a closed pocket farmEntryOk turns down, a cut-off, a
    // teammate's exit, a ram risk) while another move is safe is dropped for the next target (refuse.hpp):
    bool dropRefusedTarget = true;
    bool refuseApproach = true;  // ... at any distance, when no move nearer it is safe (false: only the step onto it)
    int refusedPocketRounds = 20; // ... and when every approach entered a closed pocket that is no farm, choosePearl
                                  //     skips it for this many rounds after the refusal; other refusals that turn only
    int exploreStride = 4;       // explore targets are sampled on a grid this coarse
    int exploreRetarget = 25;    // pick a fresh explore target at least this often (rounds)
    int staleCap = 60;           // staleness is capped here; never-seen tiles count as this
    bool portalFrontier = true;  // every unknown portal in our region is an explore candidate at any round, scored as
                                 // a never-seen tile one step past it (Targets::portalScore), not only once no
                                 // never-seen tile is left in our region
    // With portalFrontier, a pearl target gives way to the nearest unknown portal in our region when a pearl beyond it
    // comes sooner (Targets::portalOverPearl): a slow home region (Trauma's start room) no longer holds us while its
    // spawns trickle.
    bool portalBeatsPearl = true;
    // On, both rules above take an unknown portal only while a tile outside our known region may hold food (never seen,
    // or seen spawning; Targets::farFood): Autarky's portals lead into walled boxes we see are barren, and crossing into
    // them lost 8 of 28 games against the champion. Off (E0383): E0364's judge read losses on near_trauma (0.443 over
    // 22) and fortress (0.466 over 52), where the gate delays leaving a start room that holds food.
    bool portalFarFood = false;
    bool explorePathDist = false; // explore candidates are scored by path steps over known edges, not torus distance
    // An unknown portal's landing risk (landing.hpp, Targets::landingRiskSteps): the chance a blind landing goes bad
    // is a Beta posterior over the landings this dragon watched, from a prior of landingBadPrior worth
    // landingPriorWeight landings. The prior is our online r0-99 crossings' death rate within 3 rounds (91 of 676,
    // audit 1790627443151991000; it depends on the map's portal density and how crowded landings are, which the
    // watched landings stand in for); its weight is a coefficient that sets the prior's spread (sd about 0.1, the
    // per-map spread of landing deaths online, 5.6% to 29%). Off: it charges the whole chance a crosser dies, not its
    // excess over staying, and so vetoed 90% of portal picks (4731 of 5277 in 80 games), while without it our crossers
    // died within 10 rounds as often as the champion's did in the same games (0.633 of crossings v 0.641).
    bool portalLandingRisk = false;
    double landingBadPrior = 0.135;
    double landingPriorWeight = 10;
    int landingWatchRounds = 3;   // a landing we still see alive this many rounds on went well: the prior's own window
    // With portalFrontier, no unknown-portal crossing whose way back to the king's reported head (or our own head
    // without a report) does not fit the rounds left before the fold starts (Targets::fitsBeforeFold).
    bool portalFoldGuard = true;
    int exploreStaleWeight = 3;
    int exploreDistWeight = 2;
    int exploreFriendWeight = 2; // prefer explore targets far from visible teammates
    int exploreFriendCap = 12;
    // In a room only portals leave (rooms.hpp's portalOnly), the dragon nearest by path to each unknown portal edge out
    // of it heads through that edge, unless its pearl target is eaten sooner than it could cross (roomexit.hpp):
    bool portalRoomExit = true;
    // ... only for a dragon alive since round 0, whose memory holds every portal it came through, and it doesn't defer
    // to a split child (roomexit.hpp); split children start with no memory, so a portal pocket they're born in reads as
    // sealed:
    bool portalRoomExitStarters = true;
    // A dragon whose room (rooms.hpp's area) holds more of our heads in view than its free tiles carry at their lengths
    // + spaceMargin (pearls in view there counted as growth) and that is, of those heads, the nearest an exit by path
    // (the king left out) walks out through that exit instead of taking a pearl inside (jamroom.hpp, I877):
    bool leaveJam = true;
    // The leaders' crossing choice (I849, crossgate.hpp, Targets::crossGateWins): a dragon whose head stands beside an
    // unknown portal edge crosses it when a logistic model of the ladder's top three teams (Cutlery, cheji bt, team
    // 801: 62,715 of their ranked turns beside an unknown portal edge, 28 Sep on) gives more than crossGateMin chance
    // they would cross there, under the other portal rules' guards (portalFarFood when on, portalFoldGuard).
    // crossGateMin is the model's own even line: cross where they cross more often than not. The weights are the fit's,
    // on raw inputs (CrossInputs). The gate asks only before round crossModelRounds (I1143), the opening in which the
    // leaders cross more: the maximum-likelihood change point of their crossing rate by round on those turns (0.57
    // before, 0.43 after; 36-38 in each map-size band and with each official map left out). From it on the champion's
    // portal rules decide alone.
    bool crossGate = true;
    // ... only for a dragon alive since round 0 (as portalRoomExitStarters): a split child starts with no memory, so
    // every portal reads as unknown to it, the ones its team already crossed into pockets too, where the fit's children
    // inherited their parent's view. In E0438's post-mortem games (60, fresh portalrooms maps v the champion) children
    // the gate sent through died within 10 rounds 42 of 109 times, against 2 of 88 where the champion's rules decided;
    // starters 8 of 34 against 5 of 34.
    bool crossGateStarters = true;
    int crossModelRounds = 38;
    double crossGateMin = 0.5;
    double crossWBias = 1.6606;
    double crossWRound = 0.00026866;
    double crossWLength = -0.0013602;
    double crossWSeen = -0.94720;         // per share of the map the team has seen
    double crossWRegionUnseen = -1.17068; // per share of our region never seen
    double crossWRegionClosed = 0.07984;
    double crossWRegionSpawn = -0.97949;  // per share of our region's seen tiles seen holding a pearl
    double crossWRegionPearls = 0.0098743;
    double crossWRegionPortals = -0.037234;
    double crossWSpawnSeen = -0.13660;
    double crossWPearlsInView = -0.013182;
    double crossWEnemiesInView = -0.027805;
    double crossWAlliesInView = -0.025031;
    double crossWEnemySeen = -0.59806;
    double crossWSince = -0.0013064;      // per round since the edge first came into view
    double crossWCrossings = 0.025907;    // per crossing of our own
    bool exploreKeepHeading = true; // explore ties go to the grid tile straight along our facing, not to scan order
    double exploreHeadingWeight = 0.4; // ... plus this times the share of a tile's distance that lies along our facing
                                       // (1 ahead, 0 abeam, -1 behind); under 0.5, so it only breaks exact ties
    // The opening race for the middle of the map (race.hpp): a dragon born in round 0 walks to where the mirror
    // lines farther from its birth tile cross, and meanwhile takes only pearls (or fast tiles) in view or on the way.
    bool openingRace = true;
    int raceRounds = 40;         // ... before this round
    int raceMaxArea = 1024;      // ... on maps of at most this many tiles (W * H); with raceByReach (I707) instead
    bool raceByReach = true;     // ... when it reaches the target by round raceRounds, by its path when it first has one
    int raceWallSeen = 3;        // ... skipping a mirror line whose seen crossings are all kelp, at least this many
    bool bandCommit = true;      // ... and passes up home food (on no contested ground under any live symmetry) off our
                                 // shortest path to it (race.hpp raceDefers, I1118)
    bool bandContested = true;   // ... only while the enemy heads seen or heard on the band at least match ours, or
                                 // the band is not yet seen or heard of (race.hpp BandHeads, I1126)
    // Contested ground (contested.hpp, I1074): with a settled symmetry, food on a tile the enemy's mirrored start reaches
    // within contestedSlack steps of our own birth tile counts contestedWorth times in pearl and explore targets, since a
    // pearl we leave there goes to them while home food waits. Worth 2 is the zero-sum count (our pearl plus theirs
    // denied); the slack of 1 is the parity step of the move order (a lower id moves first), not a fitted value.
    bool contestedDouble = true;
    double contestedWorth = 2;
    int contestedSlack = 1;
    bool contestedKingOnce = true; // ... except for a dragon that believes it is king (I1101), which counts it once
    bool bandNumbers = false;      // ... on the band times 2a/(a+b), our heads on or heading there against theirs seen
                                   // (contested.hpp count, I1118; off: its own test, I1127)

    // --- Team belief map over sonar (belief.hpp, gossip.hpp) -------------------------------------
    bool shareKelpEdges = true;  // share local closed-region walls (kelp.hpp); a split's wall block takes a proven child
                                 // ray the mirror countdown doesn't, with the most walls outside the child's view
    bool teamGossip = true;      // send our belief map's fresh reports (and relays) over sonar
    bool pearlClaims = true;     // each dragon names its pearl target and eat round on a sonar ray (ranked like a
                                 // countdown in gossip's gather), and one that hears a claim that eats a pearl first
                                 // yields it, as to a teammate in view (claims.hpp)
    bool doorwayProbe = true;    // a head on a portal mouth probes through it, and a teammate that hears the probe
                                 // scores the crossing into it as a head-on with a teammate (doorway.hpp)
    bool sonarTeamTag = true;    // the tag also covers our team side (A or B), so the other team's copy of
                                 // this bot (local tests) can't read our records nor we theirs
    bool sonarBuildKey = true;   // the tag's key is a hash of this build's source (sonarkey.hpp), so values
                                 // from any other build (our old versions' replays) fail it; this build's own
                                 // values from another game on a map of our size still pass
    bool sonarSipTag = true;     // the tag is SipHash-2-4 over the body, our side and the map size, not splitmix
    bool sonarRoundMask = true;  // what goes on the air is sealed per round (belief.hpp's wire::seal): the body XORed
                                 // with a keyed hash of the send round, so replays can't read our reports or claims
    bool sonarDropStale = true;  // drop heard records older than we ever send (gossipMaxAge, kingReportAge) + 1
    // --- Passive sonar listener (listen.hpp) ----------------------------------------------------
    bool listen = true;          // offer heard values that fail our wire check (the enemy's) to the per-team format
                                 // table (listen_formats.hpp), detect the team and store what its reports say
    bool listenShare = true;     // ... and tell teammates which team we detected (a listen record over gossip)
    bool listenUnverified = false; // ... formats that matched under 95% on replays (or were never checked) take part too
    int listenMaxAge = 30;       // a report whose round field is older than this is read as not of that format (how
                                 // old a report another team stamps is its protocol's choice, unseen until decoded)
    // The lock is a sequential likelihood-ratio test over the enemy values heard; these are its coefficients:
    int listenLockHits = 3;      // lock onto a format once it recognised this many values, ...
    double listenLockScore = 23; // ... its log-likelihood ratio (natural log) is at least this (about 10^10) ...
    double listenLockLead = 14;  // ... and at least this far above every other team's formats (about 10^6)
    int listenFacts = 64;        // decoded facts kept, oldest dropped first
    bool listenToBelief = true;  // ... and each decoded enemy head (not a sighting of ours) goes into the belief map
                                 // as a sighting at its decoded round (Belief::noteEnemyHead), so gossip relays it,
                                 // once a team is followed and unless it is a teammate's fake (spoof.hpp's ours)
    // Where an enemy sender can be, from a heard value our check drops (infer.hpp): the tiles on the four clear lines out
    // of our body.
    bool inferSender = true;     // work out the set each turn a foreign value is heard (debug line key `infer`)
    bool inferToBelief = true;   // ... and mark its blocks enemy-likely in the belief map (Belief::noteSenders)
    double inferHit = 0.95;      // share of heard foreign values whose sender's head was in the set (E0473: 95.2% of
                                 // 65,819 in 120 online games): a measured rate, the set's weight
    // --- Spoofing (spoof.hpp) -------------------------------------------------------------------
    bool spoof = true;
    // ... once the team knows the enemy's team (listen.hpp, so it needs P.listen), fakes that pass
    // that team's own check go on the rays gossip and doorway probes leave free (not those reaching a teammate or kelp
    // in view); gossip never gives up a ray for one. loop/publishing.py keys on this exact line
    bool spoofGarbage = true;    // ... a detected team with no writer of its own (spoof.hpp's WRITERS) whose format is
                                 // a verified report with a known check gets its heard reports back at random tiles
    bool spoofTrick = true;      // ... a detected team with a verified report format gets, on a ray reaching one of
                                 // its dragons in view, a decoy in its own format placed to steer that dragon into an
                                 // obstacle or off its pearls (spoof.hpp's decoyTile); other rays keep today's fake
    int spoofTrickNear = 4;      // a decoy beside the receiver sits at least this many tiles from its head ...
    int spoofTrickFar = 8;       // ... and at most this many: an obstacle further away lures nothing
    int spoofKeep = 64;          // enemy values heard kept (the newest) to write fakes from (a memory and CPU budget)
    bool beliefExplore = true;   // explore staleness counts tiles any teammate reported, and a target a
                                 // teammate reported after we chose it is dropped
    bool beliefPearls = true;    // pearl targets use teammates' pearl sightings and countdowns
    int sharedPearlPenalty = 4;  // extra cost (in steps) of a pearl only a teammate reported
    int gossipMaxAge = 30;       // never send a report older than this many rounds
    int gossipGainCap = 20;      // a report's value: rounds fresher than what we last sent of it, capped here ...
    int gossipPearlValue = 5;    // ... plus this per pearl it announces
    int gossipCountdownMax = 20; // rank a countdown for food news only within this many rounds of its spawn
    double enemyPrior = 0.1;     // chance of an enemy head in a block nobody has reported on
    double enemyHalfLife = 8;    // rounds for a report's enemy evidence to fade halfway to the prior
    // Census of the opponent (census.hpp): each dragon counts the enemy heads it sees over a window, and those counts
    // and the union of enemy ids the team saw ride the belief gossip, so every
    // dragon can estimate the opponent's size and style (short heads per dragon-turn, distinct enemy ids, the longest
    // seen). No move reads it yet.
    bool teamCensus = true;      // keep, send and relay census records
    int censusWindow = 40;       // rounds a dragon's counts (and a record's use) cover, and twice the ids union's
                                 //     epoch; 2 to 63 (the wire's turns)
    int censusShortLen = 4;      // an enemy head with at most this many visible segments counts as short
    bool censusSpareOnly = true; // census records ride only rays the belief reports leave spare (Gossip::plan), which
                                 //     keeps their delivery level and delivers almost no census; off, the turn's
                                 //     census record competes with them (Gossip::gather) and takes about a quarter of
                                 //     the rays before the fold
    bool censusFlood = true;     // before the fold's start, an ids union whose count grew past its noise goes out on
                                 //     every ray it may take (CensusTable::toSend, Gossip::plan), not one: one ray
                                 //     reaches a teammate about half the time, so news sent on one dies out
    int censusResend = 10;       // a sender's counts go again (ours or a relay) once this many rounds fresher than
                                 //     we last sent them, and an unchanged ids union this many rounds after: how often
                                 //     a window's counts change enough to resend, a bandwidth budget until a move
                                 //     reads the census and can price it
    // A long dragon grazes where the enemy isn't (Targets::grazeAt): rivals ram our long dragons from several steps,
    // so it prefers pearl and explore targets away from enemy heads the team saw recently (in view or over sonar).
    bool grazeAwayFromEnemy = true; // a dragon at least grazeMinLength long ...
    int grazeMinLength = 12;
    // ... or with grazeByLength (I497) a dragon of any length, the price scaled by length / grazeRefLength (the
    // length at which a unit of chance costs grazeWeight steps): the length a ram near the target takes grows with ours
    bool grazeByLength = false;
    double grazeRefLength = 12;
    double grazeWeight = 6;      // ... adds this many steps to a target's cost per unit of enemy-head chance above
                                 //     enemyPrior (Belief::enemyChance), summed over its block and the 8 around it

    // --- Map symmetry (symmetry.hpp) ---------------------------------------------------------------
    bool shareSymmetry = true;   // countdowns share the mirror; split turns prefer it on the neck ray
    bool useSymmetry = true;     // find the map's mirror from pairs of tiles seen (ours, and teammates' countdowns)
    bool mirrorEdges = true;     // ... once a mirror is known, every edge seen also writes its mirror's
    bool mirrorCountdowns = true; // ... and every countdown seen its mirror's (never-spawning, next spawn, resets
                                 // watched), with teammates' countdowns read at the mirror too (targets.hpp)

    // --- Fast tiles and dead-end farms ------------------------------------------------------------
    int fastMinResets = 2;       // a tile is fast once we have watched it reset this many times ...
    int fastMaxGap = 2;          // ... to a mean countdown of at most this many rounds
    int fastMaxResets = 20;      // stop recording resets after this many (keeps old evidence bounded)
    int fastTilePenalty = 1;     // extra cost (in steps) of an out-of-sight fast tile: it is probably full
    int farmClusterRadius = 2;   // a farm tile's yield counts the farm tiles within this Chebyshev radius ...
    double farmClusterWeight = 0.5; // ... each dividing its cost by 1 + this per extra one (Targets::yieldAt; which
                                    //     tiles count, and how much, is rateYield's below)
    // With rateYield, yield counts every tile watched resetting P.fastMinResets times, not only fast ones, each
    // weighted by its pearl rate against a fast tile's: min(1, rateRefGap / its mean reset), so a patch of
    // medium-rate tiles is a farm too (Targets::yieldAt). Off, a tile counts 1 when fast and 0 otherwise.
    bool rateYield = true;
    double rateRefGap = 7;       // coefficient of that weight: the mean reset at and below which a tile counts in full
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
    // Near the unit cap, prefer an open move no other head can cut off before our next move (jam.hpp): its
    // fill with every tile another visible head can step onto as a wall stays open and big enough.
    bool headReachGuard = true;
    int headReachCapMargin = 2;       // ... from this many dragons below the unit cap
    double headReachPenalty = 2e5;    // ... the price of an open move that can be cut off, while another can't:
                                      // above any pearl/explore target difference, below one tile of shortfall
    // Short local search (search.hpp): our next moves against every reply of the enemy heads near us, steps and paid
    // sprints alike. An open move after which they can force our loss costs searchLossPenalty for each move sooner
    // than the best open move's worst case that it is lost (open moves are ranked by their worst case first).
    bool localSearch = true;
    int searchMinLength = 8;          // ... for dragons at least this long: a shorter one loses little to a ram (the
                                      // shorter rammer dies too) and much food to steering clear of every one
    // With searchOnSwing (I951, I1154), instead of searchMinLength: while some searched head's ram on us has a swing
    // for it (the ram model's, harvest.hpp's headOnSwing) above searchMinSwing, what a ram costs us beats the food that
    // steering clear gives up
    bool searchOnSwing = true;
    double searchMinSwing = 6.06;     // the swing of a 2-long head's ram on an 8-long dragon at even heads in view
    bool searchRamLoss = true;        // a bad rammer landing on our head loses the line (off: only searchBoxLoss can)
    bool searchBoxLoss = false;       // our room at the horizon short of evaluateMove's need loses it (I285's half)
    bool searchSprints = true;        // enemy rams include paid sprints (off: single steps only, as E0109) ...
    bool searchSprintMoves = false;   // ... and so do their replies that don't ram (off: those step or split)
    int searchMaxMovers = 3;          // at most the nearest this many enemy heads move in the search; others stand
    int searchMaxDepth = 3;           // deepen to this many of our moves ...
    int searchWorkLimit = 30000;      // ... while the search's work (tiles visited and nodes) stays under this ...
    int searchClockLimit = 25000000;  // ... and its CPU under this many points (steady_clock ns; wasm builds only)
    double searchLossPenalty = 3e6;   // like riskPenalty
    bool guardTeammateExit = true;    // penalise stepping onto a visible teammate's only exit (Safety::blocksTeammateExit)
    double friendExitPenalty = 3e6;   // ... occupying it before the teammate's next turn kills it
    // The squeeze guard (seal.hpp): a step after which a visible teammate's timed fill reaches no way out while
    // another step of ours keeps one costs squeezePenalty; mostly our bodies squeezing a teammate in crowded ground
    bool guardTeammateSqueeze = true;
    double squeezePenalty = 3e6;
    // ... with squeezeOnValue (I736), a step that eats is charged only while what the squeezed teammate stands to lose,
    // squeezeDeathChance x its visible length, is more than the pearl the step eats
    bool squeezeOnValue = true;
    double squeezeDeathChance = 0.139; // the guard's cut in squeezed teammates' deaths within 10 rounds (E0255: 48.4% to 34.5%)
    double attackBonus = 2000;
    double attackPerLength = 200;
    double pearlBonus = 150;          // stepping onto a pearl
    double targetWeight = 100;        // per step of distance to the pearl target
    double exploreWeight = 60;        // per step of distance to the explore target
    bool moveKeepHeading = true;      // equally scored moves go straight on, not to the first in N, E, S, W order
    double earlySpawnPenalty = 300;   // standing on a spawn tile before it fires blocks the spawn
    double spaceWeight = 3;           // per reachable tile, up to spaceCap
    double friendRepelWeight = 25;    // per unit of closeness to a teammate head
    int friendRepelRadius = 3;
    // Keep portal doorways clear (doorway.hpp): a move ending on a tile beside a known portal, not crossing one to
    // get there or next, costs doorwayCost steps of targetWeight per round our body covers the tile (our length),
    // while another penalty-free move gets as near the target, so teammates crossing blind don't land on us.
    bool keepDoorways = true;
    double doorwayCost = 3;           // coefficient of that price, in steps of targetWeight per round covered

    bool debugIndicator = false;      // the debug log line each turn (debugline.hpp; costs output bytes; keep off in the ladder)
    int dropEdgeRadius = 3;           // heads within this many steps of a ram's victim contest its drop: the ram
                                      // model's near heads (rammodel.hpp) and the debug line's redge
};

inline constexpr Params P{};
