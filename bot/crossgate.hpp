#pragma once

#include "belief.hpp"
#include "params.hpp"
#include "state.hpp"

#include <cmath>
#include <vector>

namespace bot {

// The leaders' crossing choice (I849, P.crossGate): what a dragon standing beside an unknown portal sees, and the
// chance the ladder's top three teams would cross there, from a logistic model fit on their ranked games (P.crossW*),
// asked before P.crossModelRounds.
// CrossGate remembers what World and Belief don't: the round each portal edge first came into view, our own
// crossings, and whether an enemy head was ever seen (Belief keeps only the freshest report of each block). Call
// observe() once per turn after the team's messages are heard (Belief::hear); Targets::crossGateWins asks
// crossChance.
struct CrossGate {
    std::vector<int> portalFirst; // canonical edge -> round it was first in view, -1 never
    int crossings = 0;            // our own portal crossings this process
    bool enemySeen = false;       // an enemy head was in our view, or a teammate reported one, this process
    int prevHead = -1;

    void observe(World const& w, Belief const& team) {
        for (auto const& d : w.dragons)
            enemySeen = enemySeen || (d.enemy && d.head >= 0);
        for (int b = 0; b < team.blocks() && !enemySeen; b++)
            enemySeen = team.enemyRound[b] >= 0 && team.enemyThere[b];
        if (portalFirst.empty())
            portalFirst.assign(w.edgeKind.size(), -1);
        for (int t : w.touched) {
            if (!w.visible(t))
                continue;
            for (int d = 0; d < 4; d++) {
                int const e = w.edgeOf(t, d);
                if (w.edgeKind[e] == EDGE_PORTAL && portalFirst[e] < 0)
                    portalFirst[e] = w.round;
            }
        }
        // A move through a portal lands the head beside a portal edge whose far side is the tile we left.
        if (prevHead >= 0 && prevHead != w.head && w.round > w.firstRound) {
            for (int d = 0; d < 4; d++) {
                if (w.edgeKind[w.edgeOf(w.head, d)] == EDGE_PORTAL && w.step(w.head, d) == prevHead) {
                    crossings++;
                    break;
                }
            }
        }
        prevHead = w.head;
    }
};

// What the dragon observes beside an unknown portal edge, as the fit read it from the leaders' replays (the team's
// memory standing in for what a leader's dragon knew: theirs inherit what they saw before a split). Region stats
// cover the tiles our head reaches over known edges without crossing a portal (Targets::scanRegion), unseen tiles
// counted open.
struct CrossInputs {
    int round = 0;
    int length = 0;
    double seen = 0;      // share of the map's tiles the team has seen
    double runseen = 0;   // share of the region's tiles never seen
    bool rclosed = false; // ... none
    double rspawn = 0;    // share of the region's seen tiles ever seen holding a pearl
    int rpearls = 0;      // region tiles that held a pearl when last seen
    int rupt = 0;         // unknown portal sides on the region's tiles
    double spawnseen = 0; // share of all seen tiles ever seen holding a pearl
    int pview = 0;        // pearls in view
    int eview = 0;        // enemy heads in view
    int aview = 0;        // other heads of ours in view
    bool eseen = false;   // an enemy head was seen (CrossGate::enemySeen)
    int since = 0;        // rounds since the unknown portal edge beside us first came into view (the earliest)
    int ncross = 0;       // our own portal crossings
};

// The top three teams' chance of crossing (P.crossW*: raw-scale logistic weights, fit.py-style, on 62,715 of their
// turns beside an unknown portal edge; round and length stand in for rounds left and what the dragon can still reach).
inline double crossChance(CrossInputs const& x) {
    double const z = P.crossWBias + P.crossWRound * x.round + P.crossWLength * x.length + P.crossWSeen * x.seen +
                     P.crossWRegionUnseen * x.runseen + P.crossWRegionClosed * x.rclosed +
                     P.crossWRegionSpawn * x.rspawn + P.crossWRegionPearls * x.rpearls +
                     P.crossWRegionPortals * x.rupt + P.crossWSpawnSeen * x.spawnseen + P.crossWPearlsInView * x.pview +
                     P.crossWEnemiesInView * x.eview + P.crossWAlliesInView * x.aview + P.crossWEnemySeen * x.eseen +
                     P.crossWSince * x.since + P.crossWCrossings * x.ncross;
    return 1 / (1 + std::exp(-z));
}

} // namespace bot
