#pragma once

#include "belief.hpp"
#include "claims.hpp"
#include "contested.hpp"
#include "jamroom.hpp"
#include "crossgate.hpp"
#include "landing.hpp"
#include "notes.hpp"
#include "race.hpp"
#include "roomexit.hpp"
#include "rooms.hpp"
#include "state.hpp"

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <utility>
#include <vector>

namespace bot {

// Where this dragon is heading: a pearl it reaches before every visible teammate, else a spawn tile about
// to fire, else a stale part of the map away from teammates, or an unknown portal into unseen ground (chooseExplore).
// Call update() once per turn. Pearls are ranked
// by steps to eat them; a spawn tile's steps are divided by the yield of the spawn tiles around it (fast ones, or
// with P.rateYield every one we watched reset, weighed by its rate), so a farm
// further away beats a lone pearl, and a farm is worth a trip up to `reach` steps per unit of yield (reachSteps:
// P.maxTargetDist, or with P.rateReach that scaled by what a pearl found by exploring costs this dragon).
// With the swarm full (crowdActive) a long dragon (with P.crowdByLength any, by its length) also pays for crowded
// ground round a target (crowdAt), so it goes where there is room, and a dragon at least P.grazeMinLength long (with
// P.grazeByLength any, by its length) pays for ground where the team recently saw an enemy head (grazeAt), so it
// grazes where the enemy isn't. In the opening race (race.hpp) the race
// target takes the place of exploring, and pearl targets are limited to pearls in view and ones on the way (raceKeeps),
// less home food, on no contested ground, until the band is seen or heard holding fewer enemy heads than ours
// (race.hpp raceDefers).
// A pearl whose approach the move scorer refused is skipped for a while (refuse(), refuse.hpp). In a room only portals
// leave, the round-0 dragon nearest an unknown portal edge out of it heads through that edge instead (roomexit.hpp).
// Food on contested ground (contested.hpp) counts P.contestedWorth times, on the band scaled by our heads there against
// theirs (P.bandNumbers): a pearl's yield, an explore tile's staleness (once for a dragon that believes it is king,
// P.contestedKingOnce).
// Before P.crossModelRounds, beside an unknown portal edge a dragon crosses it where the leaders' crossing model says
// they would (crossGateWins).
struct Targets {
    int pearl = -1;          // chosen pearl or spawn tile, -1 none
    int pearlReady = 0;      // step on which the target first holds a pearl (0 = it already does)
    int picked = -1;         // choosePearl's winner this turn, before a fold (fold.hpp) or seekFeeder retargets
                             // (-1 when an unknown portal beat it: pickTarget)
    double pearlCost = 0;    // ... and its cost: steps until we eat it (its wait included), plus its penalty, over its yield
    DistField pearlField;
    DistField scratch;
    DistField headField;     // from our head, built lazily once per turn to cost far farm tiles (yieldAt)

    int explore = -1;
    int explorePortalDir = -1; // direction from `explore` through an unknown portal, -1 for a plain target
    int exploreChosenRound = -1000;
    DistField exploreField;    // from `explore`, searched out to one step past our head: scoring reads only there
    std::vector<int> regionMark; // == regionStamp for tiles reachable from our head (scanRegion)
    std::vector<int> regionDist; // ... and their path steps from it
    std::vector<int> regionPortals; // ... and its unknown portal sides, tile * 4 + dir, nearest first
    bool farFood = true;            // ... and whether a tile outside it may hold food
    std::vector<int> regionQueue;
    int regionStamp = 0;

    struct Candidate {
        int tile;
        int ready;
        double penalty;   // extra steps: memory or countdown doubts, crowding, enemy heads seen near it
        double yield;     // divides the cost: 1, or more for a spawn tile among others (yieldAt) or on contested ground
        double roughCost; // a lower bound on the cost, for pruning
        int reach;        // the longest path worth taking to it: `reach` steps per unit of yield
    };
    std::vector<Candidate> candidates;

    // How many steps a pearl found by exploring costs this process. An episode runs from a turn with no pearl
    // target to the next pearl we eat and counts every turn in it, the walk to what exploring found included.
    struct ExploreRate {
        int episodes = 0; // finished episodes ...
        int steps = 0;    // ... and their turns
        int open = -1;    // turns of the episode under way, -1 none

        // Call after turn() at the start of each turn with whether we ate since the last one.
        void observe(bool ate) {
            if (ate && open >= 0) {
                episodes++;
                steps += open;
                open = -1;
            }
        }

        // Call once a turn for the last one, with whether its final pick (after any refusal) was no pearl target.
        void turn(bool exploring) {
            if (exploring && open < 0)
                open = 0;
            if (open >= 0)
                open++;
        }

        // Mean steps per pearl, the episode under way counting its turns so far, with `weight` more episodes of
        // `prior` steps each.
        double mean(double prior, double weight) const {
            return (steps + std::max(open, 0) + weight * prior) / (episodes + weight);
        }
    };
    ExploreRate exploreRate;
    int reach = P.maxTargetDist; // this turn's path reach for a pearl of yield 1 (reachSteps)
    bool updated = false;        // update() has run before, so `picked` is the last turn's final pick
    int shadowPearl = -1;        // with kNotes: choosePearl's winner under the other reach (P.rateReach flipped) ...
    int shadowReach = 0;         // ... and that reach
    int bandShadow = -1;         // with kNotes: choosePearl's winner without I1118 (bandOff) ...
    bool bandOff = false;        // ... which, while set, drops race.hpp raceDefers and contested.hpp's heads scale

    // The team's belief map (belief.hpp), set by main; nullptr leaves targeting on this dragon's own memory.
    Belief const* team = nullptr;

    // Portal landings this dragon watched (landing.hpp), set by main; nullptr prices crossings at the prior.
    Landings const* landings = nullptr;

    // Teammates' pearl claims we heard (claims.hpp), set by main; nullptr yields only to teammates in view.
    Claims const* claims = nullptr;
    // What the leaders' crossing model reads that World doesn't keep (crossgate.hpp), set by main; nullptr leaves the
    // gate (crossGateWins) off.
    CrossGate const* crossGate = nullptr;
    DistField kingField; // from the king's reported head, for the fold guard (fitsBeforeFold) ...
    int kingFieldFor = -1; // ... built in this round

    bool teamPearls() const { return team && P.beliefPearls; }
    bool teamExplore() const { return team && P.beliefExplore; }

    bool crowdOn = false; // this turn, targets pay for crowding (crowdActive) ...
    double crowdWeight = 0; // ... this many steps per teammate body tile (crowdPrice)
    std::vector<double> grazeCost; // block -> extra steps for a target in it this turn (prepareGraze), empty when off
    std::vector<double> grazeExcess;

    // With P.crowdTargets: our swarm is as full as ordinary splits let it get (the unit cap less rescueReserve), so
    // its bodies wall us in and a split out of a pocket costs our front or finds no slot, and (without
    // P.crowdByLength) we are long enough to be walled in.
    static bool crowdActive(World const& w) {
        return P.crowdTargets && w.unitCount >= w.unitLimit - rescueReserve(w) &&
               (P.crowdByLength || w.length >= P.roomMinLength);
    }

    // Steps a teammate body tile round a target costs while crowdActive: P.crowdWeight, with P.crowdByLength scaled
    // by our length over P.crowdRefLength, since being walled in costs a longer dragon more.
    static double crowdPrice(World const& w) {
        return P.crowdByLength ? P.crowdWeight * w.length / P.crowdRefLength : P.crowdWeight;
    }

    // Teammate segments (not ours) within P.crowdRadius (Chebyshev) of t, seen there in the last
    // P.crowdMemoryRounds rounds and not seen gone since.
    static int crowdAt(World const& w, int t) {
        int const rx = std::min(P.crowdRadius, (w.W - 1) / 2), ry = std::min(P.crowdRadius, (w.H - 1) / 2);
        int crowd = 0;
        for (int dy = -ry; dy <= ry; dy++) {
            int const y = (w.yOf(t) + dy + w.H) % w.H;
            for (int dx = -rx; dx <= rx; dx++) {
                int const seen = w.friendBodySeen[w.tileOf((w.xOf(t) + dx + w.W) % w.W, y)];
                if (seen >= 0 && w.round - seen <= P.crowdMemoryRounds)
                    crowd++;
            }
        }
        return crowd;
    }

    // When tile t was last seen, by us or (with P.beliefExplore) by any teammate who reported it; -1 never.
    int seenForExplore(World const& w, int t) const { return teamExplore() ? team->seen[t] : w.lastSeen[t]; }

    int race = -1;             // this turn's opening race target (race.hpp), -1 none
    int raceArrive = -1;       // the round we would reach it, from the first turn we had one (raceTarget)
    DistField raceField;       // from `race`
    BirthField bornField;      // from our birth tile while the race runs with P.bandCommit (race.hpp raceDefers)
    BandHeads bandHeads;       // ... and the heads on its band (with P.bandContested)
    bool bandFight = true;     // raceDefers may pass up home food this turn: without P.bandContested, or b >= a there
    int commitShadow = -1;     // with kNotes: choosePearl's winner with bandFight forced on (E0412's unconditional commit)
    bool exploreFromRace = false; // `explore` is a race target: once the race is over, chooseExplore picks afresh
    bool exploreFromRoom = false; // `explore` is a room exit (roomExitWins): once it isn't, chooseExplore picks afresh
    bool exploreFromJam = false;  // `explore` is a jammed room's exit (jamLeaveWins): once it isn't, likewise

    // The room map (rooms.hpp), set by main and kept current each turn before update(); nullptr leaves out the room
    // exit and the jammed room's (roomExitWins, jamLeaveWins).
    RoomMap const* rooms = nullptr;
    RoomExit roomExit;
    JamLeave jamLeave;

    Contested contested; // this turn's contested ground (update())
    bool king = false;   // set by main before update(): this dragon believes it is king (fold.hpp's isKing)

    // How many times food on tile t counts: P.contestedWorth on contested ground (on the band times its heads-in-numbers
    // scale, contested.hpp), else 1.
    double worth(int t) const {
        if (!contested.at(t))
            return 1.0;
        return P.contestedWorth * (bandOff || !contested.band(t) ? 1 : contested.scale);
    }

    // With P.grazeAwayFromEnemy, a team belief map and our length at least P.grazeMinLength (with P.grazeByLength any
    // length): fills grazeCost with P.grazeWeight steps (with P.grazeByLength times our length over P.grazeRefLength)
    // per unit of enemy-head chance above P.enemyPrior in each block and the 8 around it (the map wraps). Blocks nobody
    // reported on, or reported empty, add nothing. Otherwise leaves grazeCost empty.
    void prepareGraze(World const& w, bool on) {
        grazeCost.clear();
        if (!on || !team || (!P.grazeByLength && w.length < P.grazeMinLength))
            return;
        double const weight = P.grazeByLength ? P.grazeWeight * w.length / P.grazeRefLength : P.grazeWeight;
        int const BW = team->BW, BH = team->BH;
        grazeExcess.assign(BW * BH, 0);
        bool any = false;
        for (int b = 0; b < BW * BH; b++) {
            grazeExcess[b] = std::max(0.0, team->enemyChance(b, w.round) - P.enemyPrior);
            any = any || grazeExcess[b] > 0;
        }
        if (!any)
            return;
        grazeCost.assign(BW * BH, 0);
        // Offsets -1..1 on each axis, each neighbouring block once however few blocks the map has.
        auto const lo = [](int n) { return n >= 3 ? -1 : 0; };
        auto const hi = [](int n) { return n >= 2 ? 1 : 0; };
        for (int b = 0; b < BW * BH; b++) {
            if (grazeExcess[b] <= 0)
                continue;
            int const bx = b % BW, by = b / BW;
            for (int dy = lo(BH); dy <= hi(BH); dy++)
                for (int dx = lo(BW); dx <= hi(BW); dx++)
                    grazeCost[(by + dy + BH) % BH * BW + (bx + dx + BW) % BW] += weight * grazeExcess[b];
        }
    }

    // Extra steps a target on tile t costs this turn for enemy heads seen near it (prepareGraze).
    double grazeAt(int t) const { return grazeCost.empty() ? 0 : grazeCost[team->blockOf(t)]; }

    // The path reach for a pearl of yield 1: P.maxTargetDist, or with `rate` that times our steps per pearl found by
    // exploring (exploreRate, with P.reachPriorWeight episodes at the mean P.reachPriorSteps) over that mean, so a
    // dragon with no episodes of its own keeps P.maxTargetDist.
    int reachSteps(bool rate) const {
        if (!rate)
            return P.maxTargetDist;
        double const steps = exploreRate.mean(P.reachPriorSteps, P.reachPriorWeight);
        return static_cast<int>(std::lround(P.maxTargetDist * steps / P.reachPriorSteps));
    }

    // How many steps after we could arrive a spawn tile may fire and still be a candidate: P.spawnSlack, or with
    // P.rateSpawnSlack this dragon's steps per pearl found by exploring (exploreRate, shrunk toward P.reachPriorSteps
    // by P.reachPriorWeight episodes), so a known spawn is worth waiting for whenever the wait is no longer than
    // exploring would take to find a pearl.
    double spawnSlackSteps() const {
        return P.rateSpawnSlack ? exploreRate.mean(P.reachPriorSteps, P.reachPriorWeight) : P.spawnSlack;
    }

    // `ate`: we ate a pearl since our last turn (main passes Intake's count).
    void update(World const& w, bool ate = false) {
        crowdOn = crowdActive(w);
        crowdWeight = crowdPrice(w);
        contested.update(w, king);
        contested.count(w, claims);
        prepareGraze(w, P.grazeAwayFromEnemy);
        race = raceTarget(w, raceField, raceArrive);
        bandFight = true;
        if (race >= 0 && P.bandCommit) {
            bornField.build(w);
            if (P.bandContested) {
                bandHeads.count(w, bornField, team, claims);
                bandFight = bandHeads.theirs >= bandHeads.ours;
            }
        }
        if (updated)
            exploreRate.turn(picked < 0);
        updated = true;
        exploreRate.observe(ate);
        if constexpr (kNotes) {
            reach = shadowReach = reachSteps(!P.rateReach);
            choosePearl(w);
            shadowPearl = picked;
            reach = reachSteps(P.rateReach);
            bandOff = true;
            choosePearl(w);
            bandOff = false;
            bandShadow = picked;
            bool const fight = bandFight;
            bandFight = true;
            choosePearl(w);
            bandFight = fight;
            commitShadow = picked;
        }
        reach = reachSteps(P.rateReach);
        pickTarget(w);
    }

    // The room exit when roomExitWins, else a jammed room's exit when jamLeaveWins, else the unknown portal beside us
    // when crossGateWins, else the pearl target (outside the opening race, the unknown portal instead when a pearl
    // beyond it comes sooner: portalOverPearl), else the race target, else an explore target. update()
    // calls it; main calls it again after refuse() on a turn whose pearl target the move scorer refused (refuse.hpp),
    // which only happens with a pearl target, so the race branch below (which swaps raceField away) runs at most once a
    // turn.
    void pickTarget(World const& w) {
        jamLeave.barred = -1;
        if (P.leaveJam && rooms)
            jamLeave.bar(w, *rooms);
        jamLeave.heads = 0;
        choosePearl(w);
        if (roomExitWins(w) || jamLeaveWins(w) || crossGateWins(w))
            return;
        int const side =
            pearl >= 0 && race < 0 && P.portalFrontier && (P.portalBeatsPearl || kNotes) ? portalOverPearl(w) : -1;
        if constexpr (kNotes)
            notes.portalOverPearl = side >= 0 ? regionDist[side / 4] + 1 : -1;
        if (side >= 0 && P.portalBeatsPearl) {
            targetPortal(w, side);
            return;
        }
        if (pearl >= 0)
            return;
        if (race < 0) {
            chooseExplore(w);
            return;
        }
        explore = race;
        explorePortalDir = -1;
        exploreChosenRound = w.round;
        exploreFromRace = true;
        exploreFromRoom = exploreFromJam = false;
        std::swap(exploreField, raceField);
    }

    // With P.portalRoomExit and a room map: when roomExit gives us an unknown portal edge out of our portal-only room
    // and our pearl target (if any) is eaten no sooner than we could cross, drops the pearl target and makes the tile
    // beside that edge our explore target, crossing it (crossesExplorePortal); returns whether it did. It takes the
    // place of the race and of chooseExplore's own choice for as long as it holds.
    bool roomExitWins(World const& w) {
        if (!P.portalRoomExit || !rooms || !roomExit.choose(w, *rooms))
            return false;
        if (pearl >= 0 && std::max(pearlField.at(w.head), pearlReady) < roomExit.steps)
            return false;
        pearl = picked = -1;
        explore = roomExit.tile;
        explorePortalDir = roomExit.dir;
        exploreChosenRound = w.round;
        exploreFromRace = false;
        exploreFromRoom = true;
        exploreFromJam = false;
        exploreField.build(w, explore, w.W + w.H, w.head);
        return true;
    }

    // With P.leaveJam and a room map: when jamLeave names us the leaver of our jammed room, drops a pearl target inside
    // the room and makes the tile past the room's nearest exit our explore target; returns whether it did. A pearl
    // target outside the room stays: its path leaves the room anyway. Asked with the debug line on even while
    // P.leaveJam is off, so a logging copy counts where it would fire.
    bool jamLeaveWins(World const& w) {
        if (!rooms || !(P.leaveJam || kNotes))
            return false;
        auto const* reported = team ? team->bestKing(w.round, P.kingReportAge, -1) : nullptr;
        bool const leave = jamLeave.choose(w, *rooms, king, reported ? reported->rec.id : -1);
        if constexpr (kNotes)
            notes.jamLeave = leave ? jamLeave.steps : -1;
        if (!leave || !P.leaveJam)
            return false;
        if (pearl >= 0 && rooms->rooms[rooms->roomOf[pearl]].area != jamLeave.area)
            return false;
        pearl = picked = -1;
        explore = jamLeave.tile;
        explorePortalDir = -1;
        exploreChosenRound = w.round;
        exploreFromRace = exploreFromRoom = false;
        exploreFromJam = true;
        exploreField.build(w, explore, w.W + w.H, w.head);
        return true;
    }

    // P.crossGate: before P.crossModelRounds, when our head stands beside an unknown portal edge and the leaders'
    // crossing model (crossgate.hpp) gives them more than P.crossGateMin chance of crossing here, drops any pearl target
    // and crosses it (the first such edge, north first), in place of the rest of pickTarget. With the debug line on it also asks while the switch is
    // off, to log where it would fire (notes.crossDir). Under the guards the other portal rules have: with
    // P.portalFarFood not while every tile beyond our region was seen barren (farFood), and with P.portalFoldGuard not
    // when the crossing doesn't leave time to come back before the fold (fitsBeforeFold). With P.crossGateStarters only a
    // dragon alive since round 0 asks. Returns whether it crossed.
    bool crossGateWins(World const& w) {
        if (!(P.crossGate || kNotes) || !crossGate || w.round >= P.crossModelRounds ||
            (P.crossGateStarters && w.firstRound != 0))
            return false;
        int dirs[4];
        int n = 0;
        for (int d = 0; d < 4; d++)
            if (w.step(w.head, d) == UNKNOWN_PORTAL)
                dirs[n++] = d;
        if (n == 0)
            return false;
        scanRegion(w);
        double const chance = crossChance(crossInputs(w, dirs, n));
        bool const fires = chance > P.crossGateMin && !(P.portalFarFood && !farFood) &&
                           !(P.portalFoldGuard && !fitsBeforeFold(w, w.head));
        if constexpr (kNotes) {
            notes.crossChance = chance;
            notes.crossDir = fires ? dirs[0] : -1;
        }
        if (!fires || !P.crossGate)
            return false;
        targetPortal(w, w.head * 4 + dirs[0]);
        return true;
    }

    std::vector<int> crossMark; // == crossStamp for the tiles of crossInputs' region
    std::vector<int> crossQueue;
    int crossStamp = 0;

    // The leaders' crossing model's inputs (CrossInputs) for our head beside the unknown portal edges `dirs` (n of
    // them). What the team has seen (Belief) stands in for this dragon's memory, as a leader's split child inherits its
    // parent's; without a team belief map, our own memory. The region is what the fit read: the tiles our head reaches
    // over known edges without crossing any portal, known or not (scanRegion's goes through known ones).
    CrossInputs crossInputs(World const& w, int const* dirs, int n) {
        auto const seenAt = [&](int t) { return team ? std::max(team->seen[t], w.lastSeen[t]) : w.lastSeen[t]; };
        auto const pearlEver = [&](int t) { return w.pearlSeen[t] >= 0 || (team && team->pearl[t] >= 0); };
        auto const pearlLast = [&](int t) {
            return (w.lastSeen[t] >= 0 && w.pearlSeen[t] == w.lastSeen[t]) ||
                   (team && team->seen[t] >= 0 && team->pearl[t] == team->seen[t] && team->seen[t] >= w.lastSeen[t]);
        };
        CrossInputs x;
        x.round = w.round;
        x.length = w.length;
        int seen = 0, spawn = 0;
        for (int t = 0; t < w.N; t++) {
            if (seenAt(t) < 0)
                continue;
            seen++;
            spawn += pearlEver(t);
        }
        x.seen = static_cast<double>(seen) / w.N;
        x.spawnseen = seen ? static_cast<double>(spawn) / seen : 0;
        if (crossMark.empty()) {
            crossMark.assign(w.N, 0);
            crossQueue.reserve(w.N);
        }
        crossStamp++;
        crossQueue.clear();
        crossQueue.push_back(w.head);
        crossMark[w.head] = crossStamp;
        int unseen = 0, rseen = 0, rspawn = 0;
        for (std::size_t at = 0; at < crossQueue.size(); at++) {
            int const cur = crossQueue[at];
            if (seenAt(cur) < 0) {
                unseen++;
            } else {
                rseen++;
                rspawn += pearlEver(cur);
                x.rpearls += pearlLast(cur);
            }
            for (int dir = 0; dir < 4; dir++) {
                if (w.edgeKind[w.edgeOf(cur, dir)] == EDGE_PORTAL) {
                    x.rupt += w.step(cur, dir) == UNKNOWN_PORTAL;
                    continue;
                }
                int const to = w.step(cur, dir);
                if (to < 0 || crossMark[to] == crossStamp)
                    continue;
                crossMark[to] = crossStamp;
                crossQueue.push_back(to);
            }
        }
        x.runseen = static_cast<double>(unseen) / static_cast<double>(crossQueue.size());
        x.rclosed = unseen == 0;
        x.rspawn = static_cast<double>(rspawn) / std::max(rseen, 1);
        for (auto const& d : w.dragons) {
            if (d.head < 0 || d.id == w.myId || !w.visible(d.head))
                continue;
            (d.enemy ? x.eview : x.aview)++;
        }
        for (int t : w.touched)
            x.pview += w.visible(t) && w.pearlNow[t];
        x.eseen = x.eview > 0 || crossGate->enemySeen;
        int first = w.round;
        for (int i = 0; i < n && !crossGate->portalFirst.empty(); i++) {
            int const f = crossGate->portalFirst[w.edgeOf(w.head, dirs[i])];
            if (f >= 0)
                first = std::min(first, f);
        }
        x.since = w.round - first;
        x.ncross = crossGate->crossings;
        return x;
    }

    // Pearl targets the move scorer refused to approach (refuse.hpp), each skipped by choosePearl up to its last round.
    struct Refusal {
        int tile;
        int until;
    };
    std::vector<Refusal> refusals;

    bool refused(World const& w, int t) const {
        for (auto const& r : refusals)
            if (r.tile == t && w.round <= r.until)
                return true;
        return false;
    }

    // Records that the move scorer refused to approach pearl target t this turn: choosePearl skips it this round
    // and `rounds` more. Drops expired refusals.
    void refuse(World const& w, int t, int rounds) {
        std::erase_if(refusals, [&](Refusal const& r) { return w.round > r.until; });
        refusals.push_back({t, w.round + rounds});
    }

    // During the opening race, a pearl target must be a pearl (or fast tile) in view, or nearer the race target than
    // our head: the race doesn't stop for spawn tiles and remembered pearls in our home area (choosePearl also passes
    // up the home food race.hpp raceDefers names).
    bool raceKeeps(World const& w, int t) const {
        return race < 0 || (w.visible(t) && (w.pearlNow[t] || w.fastTile(t))) || raceField.at(t) < raceField.at(w.head);
    }

    // How much tile t counts towards a farm's yield. With P.rateYield: 0 until we watched it reset P.fastMinResets
    // times, then its pearl rate against a fast tile's, min(1, P.rateRefGap / its mean reset). Off: 1 for a fast
    // tile, else 0. Only tiles in resetTiles count.
    static double rateWeight(World const& w, int t) {
        if (!P.rateYield)
            return w.fastTile(t) ? 1 : 0;
        if (w.resetCount[t] < P.fastMinResets)
            return 0;
        double const gap = static_cast<double>(w.resetSum[t]) / w.resetCount[t];
        return gap <= P.rateRefGap ? 1 : P.rateRefGap / gap;
    }

    // A counting tile's yield (rateWeight above 0): 1 plus P.farmClusterWeight times the summed rateWeight of the
    // other tiles within P.farmClusterRadius. Any other tile's is 1.
    static double yieldAt(World const& w, int t) {
        if (rateWeight(w, t) <= 0)
            return 1;
        double others = 0;
        for (int dy = -P.farmClusterRadius; dy <= P.farmClusterRadius; dy++) {
            for (int dx = -P.farmClusterRadius; dx <= P.farmClusterRadius; dx++) {
                int const x = (w.xOf(t) + dx + w.W) % w.W, y = (w.yOf(t) + dy + w.H) % w.H;
                int const near = w.tileOf(x, y);
                if (near != t)
                    others += rateWeight(w, near);
            }
        }
        return 1 + P.farmClusterWeight * others;
    }

    // The drop pearls in view within P.dropEdgeRadius (Chebyshev) of drop tile t, t included (P.harvestDrops).
    static double dropYield(World const& w, int t) {
        int const r = P.dropEdgeRadius;
        int drops = 0;
        for (int dy = -r; dy <= r; dy++)
            for (int dx = -r; dx <= r; dx++)
                drops += w.dropNow(w.tileOf((w.xOf(t) + dx + w.W) % w.W, (w.yOf(t) + dy + w.H) % w.H)) ? 1 : 0;
        return std::max(1, drops);
    }

    void add(int t, int ready, double penalty, int rough, double yield) {
        candidates.push_back({t, ready, penalty, yield, (std::max(rough, ready) + penalty) / yield,
                              static_cast<int>(reach * yield)});
    }

    // Adds tile t as a candidate if it holds, or will soon hold, a pearl close enough to be worth a look.
    // `rough` is its torus distance from our head; headFieldBuilt says whether headField is this turn's.
    void consider(World const& w, int t, int rough, bool& headFieldBuilt) {
        if (t == w.head)
            return;
        double const yield = yieldAt(w, t) * worth(t);
        if (rough > reach * yield)
            return;
        if (yield > 1) {
            // Farms (and contested ground, worth more than one) sit behind kelp, where the straight-line distance says
            // little: use the real path.
            if (!headFieldBuilt) {
                headField.build(w, w.head, w.W + w.H);
                headFieldBuilt = true;
            }
            rough = headField.at(t);
            if (rough > reach * yield)
                return;
        }
        auto const add = [&](int ready, int penalty) {
            double const crowd = crowdOn && rough > P.crowdFreeDist ? crowdWeight * crowdAt(w, t) : 0;
            this->add(t, ready, penalty + crowd + grazeAt(t), rough, yield);
        };
        // With kNotes, names the candidate for the debug line's tk.
        auto const kind = [&](char const* name) {
            if constexpr (kNotes)
                kindAt()[t] = name;
        };
        if (w.visible(t)) {
            if (P.harvestDrops && w.dropNow(t)) {
                kind("drop");
                double const drops = dropYield(w, t) * worth(t);
                this->add(t, 0, grazeAt(t), rough, drops);
                return;
            }
            if (w.pearlNow[t]) {
                kind("visible");
                add(0, 0);
                return;
            }
        } else if (w.pearlSeen[t] >= 0 && w.pearlSeen[t] == w.lastSeen[t] &&
                   w.round - w.lastSeen[t] <= P.pearlMemoryRounds) {
            kind("remembered");
            add(0, P.rememberedPenalty);
            return;
        } else if (teamPearls() && team->pearlAt(t) && team->seen[t] > w.lastSeen[t] &&
                   w.round - team->seen[t] <= P.pearlMemoryRounds) {
            kind("team");
            add(0, P.rememberedPenalty + P.sharedPearlPenalty);
            return;
        } else if (w.fastTile(t)) {
            kind("fast");
            add(0, P.fastTilePenalty);
            return;
        }
        // Countdowns: our own (or our mirrored write, symmetry.hpp), or the freshest one the team reported, at
        // t or, once the map's symmetry is known, at its mirror image. A reading at the image taken after t's
        // known spawn is the next draw, and says nothing of the pearl t probably holds: it doesn't replace that.
        int fires = w.spawnKind[t] == SPAWN_YES ? w.spawnRound[t] : -1;
        int lastSeen = w.lastSeen[t];
        if (teamPearls() && w.spawnKind[t] != SPAWN_NEVER) {
            int read = w.spawnKind[t] == SPAWN_YES ? w.spawnRead[t] : -1;
            if (team->spawnSeen[t] >= read) {
                fires = team->spawn[t];
                read = team->spawnSeen[t];
            }
            lastSeen = team->seen[t];
            if (w.symmetry >= 0 && P.mirrorCountdowns) {
                int const m = w.mirrorTile(t, w.symmetry);
                bool const probable = fires >= 0 && fires <= team->spawnSeen[m] && lastSeen < fires &&
                                      w.round - fires <= P.pearlMemoryRounds;
                if (team->spawnSeen[m] > read && !probable)
                    fires = team->spawn[m];
            }
        }
        if (fires < 0)
            return;
        if (fires <= w.round) {
            if (lastSeen < fires && w.round - fires <= P.pearlMemoryRounds) {
                kind("probable");
                add(0, P.probablePenalty);
            }
        } else {
            int const ready = fires - w.round + 1;
            if (ready <= rough + spawnSlackSteps() && !(w.visible(t) && w.occId[t] >= 0 && w.fastTile(t))) {
                kind("spawn");
                add(ready, 0);
            }
        }
    }

    // A fight's drop before it falls (P.harvestFights): a visible enemy head with a teammate's head (not ours) within
    // P.dropEdgeRadius steps of it is a target worth the pearls it would drop, ceil(visible length / 2), as a drop
    // cluster is. The move scorer's ram guard keeps us off the tiles beside it unless ramming it is worth it, so we
    // wait at its edge for the collision.
    void addFights(World const& w) {
        for (auto const& d : w.dragons) {
            if (!d.enemy || d.head < 0)
                continue;
            bool fight = false;
            for (auto const& f : w.dragons)
                fight = fight || (!f.enemy && f.id != w.myId && f.head >= 0 &&
                                  w.torusDist(f.head, d.head) <= P.dropEdgeRadius);
            int const rough = w.torusDist(w.head, d.head);
            if (fight && rough > 1) {
                add(d.head, 0, 0, rough, std::max(1, (d.visibleLen + 1) / 2));
                if constexpr (kNotes)
                    kindAt()[d.head] = "fight";
            }
        }
    }

    // True when tile t holds a visible enemy head (a fight target, addFights; the fold never keeps one).
    static bool enemyHeadAt(World const& w, int t) {
        return w.occId[t] >= 0 && w.occHead[t] && w.occEnemy[t];
    }

    // The P.maxTargetCandidates candidates with the lowest rough cost (ties to the lower tile).
    void gatherCandidates(World const& w) {
        candidates.clear();
        bool headFieldBuilt = false;
        // Only a counting tile (rateWeight above 0, all in resetTiles), a drop or contested ground can yield more than 1
        // and so be worth a trip past `reach`: the rest need only be looked at in that diamond round our head (widened
        // by contested food's worth while contested.on). The order we visit tiles in does not matter, since the
        // candidates are sorted by (cost, tile).
        double const most = P.contestedWorth * (bandOff ? 1 : std::max(1.0, contested.scale));
        int const radius = contested.on ? static_cast<int>(reach * std::max(1.0, most)) : reach;
        if (2 * radius + 1 > w.W || 2 * radius + 1 > w.H) {
            for (int t = 0; t < w.N; t++)
                consider(w, t, w.torusDist(w.head, t), headFieldBuilt);
        } else {
            int const hx = w.xOf(w.head), hy = w.yOf(w.head);
            for (int dy = -radius; dy <= radius; dy++) {
                int const y = (hy + dy + w.H) % w.H;
                int const span = radius - std::abs(dy);
                for (int dx = -span; dx <= span; dx++) {
                    int const t = w.tileOf((hx + dx + w.W) % w.W, y);
                    if (rateWeight(w, t) <= 0)
                        consider(w, t, std::abs(dx) + std::abs(dy), headFieldBuilt);
                }
            }
            for (int t : w.resetTiles)
                if (rateWeight(w, t) > 0)
                    consider(w, t, w.torusDist(w.head, t), headFieldBuilt);
        }
        if (P.harvestFights)
            addFights(w);
        auto const keep = candidates.begin() + std::min<std::size_t>(candidates.size(), P.maxTargetCandidates);
        std::partial_sort(candidates.begin(), keep, candidates.end(), [](Candidate const& a, Candidate const& b) {
            return a.roughCost != b.roughCost ? a.roughCost < b.roughCost : a.tile < b.tile;
        });
        candidates.erase(keep, candidates.end());
    }

    // True when a visible teammate gets to `tile` first (ties go to the lower id), or, optionally, when
    // an enemy head gets there strictly first. With kNotes, notes.yieldWhy says which ("mate", "tie", "enemy").
    bool someoneElseFirst(World const& w, DistField const& field, int ready, int myEat) const {
        for (auto const& d : w.dragons) {
            if (d.id == w.myId)
                continue;
            int const theirEat = std::max(field.at(d.head), ready);
            if (d.enemy) {
                if (P.yieldToEnemyCloser && theirEat < myEat) {
                    if constexpr (kNotes)
                        notes.yieldWhy = "enemy";
                    return true;
                }
            } else if (theirEat < myEat || (theirEat == myEat && d.id < w.myId)) {
                if constexpr (kNotes)
                    notes.yieldWhy = theirEat < myEat ? "mate" : "tie";
                return true;
            }
        }
        return false;
    }

    // With P.pearlClaims, true when a teammate's claim we heard (claims.hpp) eats `tile` before our `myEat` steps.
    bool claimedFirst(World const& w, int tile, int myEat) const {
        if (!P.pearlClaims || !claims || !claims->beaten(w, tile, myEat))
            return false;
        if constexpr (kNotes)
            notes.yieldWhy = "claim";
        return true;
    }

    // The cheapest candidate we reach before any teammate (see someoneElseFirst, and claimedFirst out of view). With P.pearlKeepHeading,
    // equally cheap ones go to the tile most nearly straight along our facing, not the lowest tile (north, then west).
    // With kNotes it leaves the winner's kind in notes.pearlKind, and in notes.noPearl why the cheapest pearl in view
    // was turned down: "far" (no path within its reach), "slack" (it holds a pearl too late), "home" (the race passes
    // it up, race.hpp raceDefers) or someoneElseFirst's reason, or "refused" for a pearl the move scorer recently
    // refused to approach (refused()), which is skipped.
    void choosePearl(World const& w) {
        pearl = -1;
        if constexpr (kNotes) {
            kindAt().resize(w.N);
            notes.pearlKind = notes.noPearl = nullptr;
        }
        // A turned-down candidate's reason; nullptr for someoneElseFirst's (notes.yieldWhy).
        auto const turnDown = [&](int t, char const* why) {
            if constexpr (kNotes)
                if (!notes.noPearl && w.visible(t) && w.pearlNow[t])
                    notes.noPearl = why ? why : notes.yieldWhy;
        };
        gatherCandidates(w);
        bool const keepHeading = P.pearlKeepHeading;
        double bestCost = DistField::FAR, bestShare = -2;
        // Whether cost could still win: beat bestCost, or tie it when the heading breaks ties.
        auto const canWin = [&](double cost) { return cost < bestCost || (keepHeading && cost == bestCost); };
        for (auto const& c : candidates) {
            if (!canWin(c.roughCost))
                break;
            if (!raceKeeps(w, c.tile))
                continue;
            if (!refusals.empty() && refused(w, c.tile)) {
                turnDown(c.tile, "refused");
                continue;
            }
            if (jamLeave.barred >= 0 && rooms->rooms[rooms->roomOf[c.tile]].area == jamLeave.barred) {
                turnDown(c.tile, "jam");
                continue;
            }
            // Only a path of at most `depth` steps can win, so the search stops there; a winner's field comes
            // out the same as searching out to c.reach.
            int depth = c.reach;
            while (depth >= 0 && !canWin((std::max(depth, c.ready) + c.penalty) / c.yield))
                depth--;
            if (depth < 0)
                continue;
            scratch.build(w, c.tile, depth + 1, w.head);
            int const myDist = scratch.at(w.head);
            if (myDist > depth) {
                turnDown(c.tile, "far");
                continue;
            }
            if (!bandOff && raceDefers(w, raceField, bornField, race, c.tile, myDist, bandFight)) {
                turnDown(c.tile, "home");
                continue;
            }
            if (c.ready > myDist + spawnSlackSteps()) {
                turnDown(c.tile, "slack");
                continue;
            }
            int const myEat = std::max(myDist, c.ready);
            if (!((P.harvestDrops && w.dropNow(c.tile)) || (P.harvestFights && enemyHeadAt(w, c.tile))) &&
                (someoneElseFirst(w, scratch, c.ready, myEat) || claimedFirst(w, c.tile, myEat))) {
                turnDown(c.tile, nullptr);
                continue;
            }
            double const cost = (myEat + c.penalty) / c.yield;
            double const share = keepHeading ? headingShare(w, c.tile) : 0;
            if (cost < bestCost || (keepHeading && cost == bestCost && share > bestShare)) {
                bestCost = cost;
                bestShare = share;
                pearlCost = cost;
                pearl = c.tile;
                pearlReady = c.ready;
                std::swap(pearlField, scratch);
            }
        }
        picked = pearl;
        if constexpr (kNotes) {
            notes.chosenPearl = pearl;
            notes.pearlKind = pearl >= 0 ? kindAt()[pearl] : nullptr;
        }
    }

    // A stale tile we can walk to, not too far, far from visible teammates. Kept until it comes into view or
    // goes stale. Vision stops at portals, so an unknown portal (its far end unseen) is a door into unseen ground:
    // with P.portalFrontier every unknown portal side in our region is a frontier candidate beside the stale tiles,
    // scored as a never-seen tile one step past the portal tile (portalScore), at any round. Without it (the old rule)
    // the target becomes the nearest tile beside such a portal only once the region holds no never-seen tile
    // (scanRegion). explorePortalDir says which way to cross (evaluateMove steps through it);
    // we land beside the partner edge, which pairs the portal, and pick a fresh target.
    void chooseExplore(World const& w) {
        bool const reached =
            explore >= 0 && (w.visible(explore) ||
                             (explorePortalDir >= 0 && w.step(explore, explorePortalDir) != UNKNOWN_PORTAL) ||
                             (explorePortalDir < 0 && seenForExplore(w, explore) > exploreChosenRound));
        if (explore < 0 || reached || exploreFromRace || exploreFromRoom || exploreFromJam ||
            w.round - exploreChosenRound >= P.exploreRetarget) {
            exploreFromRace = exploreFromRoom = exploreFromJam = false;
            bool const unseenLeft = scanRegion(w);
            ExplorePick const pick = P.portalFrontier ? frontierPick(w) : fallbackPick(w, unseenLeft);
            if constexpr (kNotes) {
                ExplorePick const other = P.portalFrontier ? fallbackPick(w, unseenLeft) : frontierPick(w);
                notes.exploreShadow = other.tile == pick.tile && other.dir == pick.dir ? -2 : other.tile;
                notes.exploreShadowPortal = other.dir >= 0;
                notes.exploreShadowDist = other.tile >= 0 ? regionDist[other.tile] + (other.dir >= 0) : -1;
                notes.exploreDist = pick.tile >= 0 ? regionDist[pick.tile] + (pick.dir >= 0) : -1;
            }
            explore = pick.tile;
            explorePortalDir = pick.dir;
            exploreChosenRound = w.round;
        }
        if (explore >= 0)
            exploreField.build(w, explore, w.W + w.H, w.head);
    }

    // An explore target: a tile, and the direction through the unknown portal beside it (-1 for a plain target).
    struct ExplorePick {
        int tile = -1;
        int dir = -1;
    };

    // The old rule (P.portalFrontier off): the nearest unknown portal once the region holds no never-seen tile, else
    // (or with no such portal) the best stale tile. Call after scanRegion.
    ExplorePick fallbackPick(World const& w, bool unseenLeft) {
        int const portalSide = regionPortals.empty() ? -1 : regionPortals.front();
        double score = 0;
        int const best = unseenLeft || portalSide < 0 ? bestStaleTile(w, score) : -1;
        if (best < 0 && portalSide >= 0)
            return {portalSide / 4, portalSide % 4};
        return {best, -1};
    }

    // P.portalFrontier: the best of the stale tiles and the unknown portal sides in our region, each by its explore
    // score (a portal's is portalScore). With P.portalFarFood a portal is a candidate only while a tile outside our
    // region may hold food (farFood): once everything beyond our known walls was seen barren (Autarky's empty portal
    // boxes), a portal can only lead to ground we can walk to or to nothing. Call after scanRegion.
    ExplorePick frontierPick(World const& w) {
        double bestScore = -1e18;
        ExplorePick pick{bestStaleTile(w, bestScore), -1};
        if (P.portalFarFood && !farFood)
            return pick;
        for (int side : regionPortals) {
            if (P.portalFoldGuard && !fitsBeforeFold(w, side / 4))
                continue;
            double const score = portalScore(w, side / 4);
            if (score > bestScore) {
                bestScore = score;
                pick = {side / 4, side % 4};
            }
        }
        return pick;
    }

    // An unknown portal beside tile t as explore frontier: its far side is ground we have never seen, one step past t.
    // With P.portalLandingRisk it pays landingRiskSteps.
    double portalScore(World const& w, int t) const {
        double const risk = P.portalLandingRisk ? landingRiskSteps(w) : 0;
        return exploreScore(w, t, P.staleCap, exploreDist(w, t) + 1) - P.exploreDistWeight * risk;
    }

    // A blind landing's expected loss in steps of walking: the chance it goes bad (Landings::badChance, the prior
    // without a record) times what we lose, our length plus the unit a death costs (P.childLength, what a new dragon
    // takes; none at the ceiling where ordinary splits stop, where the next split refills the slot), each length a
    // dragon's rounds of eating at the team's measured mean growth P.capFeedTeamGrowth.
    double landingRiskSteps(World const& w) const {
        double const bad = landings ? landings->badChance() : P.landingBadPrior;
        int const unit = w.unitCount >= w.unitLimit - rescueReserve(w) ? 0 : P.childLength;
        return bad * (w.length + unit) / P.capFeedTeamGrowth;
    }

    // P.portalFoldGuard: whether crossing the unknown portal beside tile t leaves time to come back to the king by the
    // time the fold needs us: by its start (foldStart), or once it runs by the last round we could still be eaten
    // (fold.hpp's foldPathCap). The way there and through, turning round beyond it (our neck lies back through the
    // portal: at least the 4 moves of the smallest loop, and until our tail has come through, length + 1), the step
    // back through, and on over known edges (portal pairs included) to the king's reported head, or to our own head
    // while no fresh report names a teammate we merge into or its head is out of reach over known edges (the fold
    // can't reach it from here either, fold.hpp). Always true without P.finalFold.
    bool fitsBeforeFold(World const& w, int t) {
        if (!P.finalFold)
            return true;
        int const deadline = w.round < foldStart() ? foldStart() : unswbc::Constants::MAX_ROUNDS - 1 - w.length;
        int const turn = std::max(4, w.length + 1) + 1;
        int const budget = deadline - w.round - (regionDist[t] + 1) - turn; // steps left for the way on from t
        if (budget < 0)
            return false;
        auto const* k = team ? team->bestKing(w.round, P.kingReportAge, w.myId) : nullptr;
        if (!k || !mergesInto(w.length, w.myId, k->rec.length, k->rec.id) || k->rec.where >= w.N)
            return regionDist[t] <= budget;
        if (kingFieldFor != w.round) {
            kingField.build(w, k->rec.where, w.N); // whole: FAR then means out of reach, not only too far
            kingFieldFor = w.round;
        }
        return kingField.at(t) < DistField::FAR ? kingField.at(t) <= budget : regionDist[t] <= budget;
    }

    // P.portalBeatsPearl: the nearest unknown portal side in our region (tile * 4 + dir; with P.portalFoldGuard the
    // nearest that fitsBeforeFold) when a pearl beyond it comes sooner than our pearl target (pearlCost), else -1. The
    // ground beyond is unseen, so a pearl there costs the steps to cross, then the mean steps per pearl our exploring
    // finds (P.reachPriorSteps, the average over our games, not this dragon's own rate: that was measured on this
    // side), plus with P.portalLandingRisk the landing's expected loss. With P.portalFarFood none while no tile outside
    // our region may hold food (farFood). Call with a pearl target (choosePearl).
    int portalOverPearl(World const& w) {
        scanRegion(w);
        if (P.portalFarFood && !farFood)
            return -1;
        double const beyond = P.reachPriorSteps + (P.portalLandingRisk ? landingRiskSteps(w) : 0);
        for (int side : regionPortals) {
            if (P.portalFoldGuard && !fitsBeforeFold(w, side / 4))
                continue;
            return regionDist[side / 4] + 1 + beyond < pearlCost ? side : -1;
        }
        return -1;
    }

    // Makes the unknown portal side `side` (tile * 4 + dir) our target in place of any pearl.
    void targetPortal(World const& w, int side) {
        pearl = picked = -1;
        explore = side / 4;
        explorePortalDir = side % 4;
        exploreChosenRound = w.round;
        exploreFromRace = exploreFromRoom = exploreFromJam = false;
        exploreField.build(w, explore, w.W + w.H, w.head);
    }

    // True when this turn's move `dir` crosses the unknown portal our explore target sits beside.
    bool crossesExplorePortal(World const& w, int dir) const {
        return pearl < 0 && explorePortalDir == dir && w.head == explore;
    }

    // BFS from our head over known edges (bodies ignored: they move) into regionMark, with each tile's path steps in
    // regionDist and every side (tile * 4 + dir) of a portal whose far end is unknown in regionPortals, nearest first,
    // and in farFood whether a tile outside the region may hold food: never seen, or seen spawning (not SPAWN_NEVER).
    // Returns whether the region holds a tile this dragon has never seen.
    bool scanRegion(World const& w) {
        if (regionMark.empty()) {
            regionMark.assign(w.N, 0);
            regionDist.assign(w.N, 0);
            regionQueue.reserve(w.N);
        }
        regionStamp++;
        regionQueue.clear();
        regionPortals.clear();
        regionQueue.push_back(w.head);
        regionMark[w.head] = regionStamp;
        regionDist[w.head] = 0;
        bool unseen = false;
        for (std::size_t at = 0; at < regionQueue.size(); at++) {
            int const cur = regionQueue[at];
            unseen = unseen || w.lastSeen[cur] < 0;
            for (int dir = 0; dir < 4; dir++) {
                int const to = w.step(cur, dir);
                if (to == UNKNOWN_PORTAL)
                    regionPortals.push_back(cur * 4 + dir);
                if (to < 0 || regionMark[to] == regionStamp)
                    continue;
                regionMark[to] = regionStamp;
                regionDist[to] = regionDist[cur] + 1;
                regionQueue.push_back(to);
            }
        }
        farFood = false;
        for (int t = 0; t < w.N && !farFood; t++)
            farFood = regionMark[t] != regionStamp && w.spawnKind[t] != SPAWN_NEVER;
        return unseen;
    }

    // How far an explore target at region tile t is: path steps over known edges with P.explorePathDist (a winding
    // walk costs its length), else the straight torus distance.
    int exploreDist(World const& w, int t) const {
        return P.explorePathDist ? regionDist[t] : w.torusDist(w.head, t);
    }

    // How much of the way to tile t lies along our facing: 1 straight ahead, 0 abeam, -1 straight behind.
    // A tile half the torus away on an axis is as near either way round, so that axis counts as ahead.
    static double headingShare(World const& w, int t) {
        int const heading = w.facing[w.head];
        auto const wrap = [](int d, int size, int ahead) {
            if (2 * d > size)
                d -= size;
            else if (2 * d < -size)
                d += size;
            return 2 * std::abs(d) == size && ahead != 0 ? ahead * std::abs(d) : d;
        };
        int const dx = wrap(w.xOf(t) - w.xOf(w.head), w.W, DX[heading]);
        int const dy = wrap(w.yOf(t) - w.yOf(w.head), w.H, DY[heading]);
        int const dist = std::abs(dx) + std::abs(dy);
        return dist == 0 ? 0.0 : static_cast<double>(dx * DX[heading] + dy * DY[heading]) / dist;
    }

    // An explore target's score (higher is better): `stale` rounds since tile t was seen (capped; bestStaleTile counts
    // it P.contestedWorth times on contested ground), `dist` steps
    // away (exploreDist), far from visible teammates' heads, while crowdOn not crowded with their bodies, and
    // (grazeAt) away from enemy heads.
    double exploreScore(World const& w, int t, double stale, int dist) const {
        int friendDist = P.exploreFriendCap;
        for (auto const& d : w.dragons)
            if (!d.enemy && d.id != w.myId)
                friendDist = std::min(friendDist, w.torusDist(t, d.head));
        double score = 1.0 * P.exploreStaleWeight * stale - 1.0 * P.exploreDistWeight * dist +
                       1.0 * P.exploreFriendWeight * friendDist;
        if (crowdOn)
            score -= P.exploreDistWeight * crowdWeight * crowdAt(w, t);
        score -= P.exploreDistWeight * grazeAt(t);
        if (P.exploreKeepHeading)
            score += P.exploreHeadingWeight * headingShare(w, t);
        return score;
    }

    // The best out-of-sight tile of the explore grid inside our region (scanRegion first) by exploreScore, -1 when
    // none beats bestScore, which it raises to the winner's.
    int bestStaleTile(World const& w, double& bestScore) const {
        bestScore = -1e18;
        int best = -1;
        for (int y = w.yOf(w.head) % P.exploreStride; y < w.H; y += P.exploreStride) {
            for (int x = w.xOf(w.head) % P.exploreStride; x < w.W; x += P.exploreStride) {
                int const t = w.tileOf(x, y);
                if (w.visible(t) || regionMark[t] != regionStamp)
                    continue;
                int const seen = seenForExplore(w, t);
                int const stale = seen < 0 ? P.staleCap : std::min(P.staleCap, w.round - seen);
                double const score = exploreScore(w, t, stale * worth(t), exploreDist(w, t));
                if (score > bestScore) {
                    bestScore = score;
                    best = t;
                }
            }
        }
        return best;
    }
};

} // namespace bot
