#pragma once

#include "ram.hpp"
#include "rammodel.hpp"
#include "safety.hpp"
#include "scoring.hpp"
#include "state.hpp"

#include <algorithm>
#include <array>
#include <tuple>
#include <vector>

namespace bot {

// Legacy enemy sprint reach without pearls, retained by shedLength when P.fullEnemyReach is off.
// The full model is in enemy_reach.hpp; our own rams use ownRamReach.
inline int ramReach(int length) { return std::min(P.maxRamSteps, std::max(1, length - 1)); }

// Our body (head first) as it would lie one step short of the enemy head at `victim` after walking there along a
// shortest path of `field` (built from our head with emptyOnly, as huntDirection builds it): the path back from the
// victim's neighbour, then our current body, cut to our length (pearls on the way are left out). The hunt prices an
// even head's drop with it, since at the ram our body lies beside the victim, not where it is now.
inline std::vector<int> approachBody(World const& w, DistField const& field, int victim) {
    std::vector<int> body;
    for (int cur = victim; field.at(cur) > 1 && static_cast<int>(body.size()) < w.length;) {
        int next = -1;
        for (int dir = 0; dir < 4 && next < 0; dir++) {
            int const n = w.step(cur, dir);
            if (n >= 0 && field.at(n) == field.at(cur) - 1 && w.occId[n] < 0 && w.visible(n))
                next = n;
        }
        if (next < 0)
            break;
        body.push_back(next);
        cur = next;
    }
    body.insert(body.end(), w.body.begin(), w.body.end());
    body.resize(std::min<std::size_t>(body.size(), std::max(w.length, 1)));
    return body;
}

// Closing in to ram: findRam only fires once an enemy head is within ownRamReach free steps, and nothing
// else walks a short dragon there. With P.closeInToRam, a dragon of length <= P.hunterMaxLength that is not
// outnumbered in view (Safety::headEdge >= P.huntMinEdge) picks a visible enemy head beyond ownRamReach but at most
// P.huntMaxDist steps over visible empty tiles (the tiles findRam walks): one strictly longer than us that the hand
// rule would ram (Safety::attackGood, even with P.ramModel on, so on reaching it findRam's gate may still decline
// it), or with P.huntEqualPrey one exactly our length whose ram findRam's gate would take once we are beside it
// (ramWorthFor with approachBody: with P.evenTradeHarvest, whether our other heads reach more of its drop first); the
// longest, then the nearest, then the lowest id. It returns the safe move (MoveEval::safe) that most shortens the path
// to that head, preferring one that eats a pearl, then the higher score, then N, E, S, W order; or -1 to play
// normally, including when no safe move gets closer. `field` is scratch space. main() plays it after findRam, findEscape and shedLength and before splitting.
inline int huntDirection(World const& w, Safety const& safety, std::array<MoveEval, 4> const& moves,
                         DistField& field) {
    if (!P.closeInToRam || w.length > P.hunterMaxLength || safety.headEdge < P.huntMinEdge)
        return -1;
    SeenDragon const* prey = nullptr;
    int preyDist = DistField::FAR;
    bool built = false;
    for (auto const& d : w.dragons) {
        if (!d.enemy || d.head < 0 || d.visibleLen < w.length)
            continue;
        bool const equal = d.visibleLen == w.length;
        if (equal ? !P.huntEqualPrey : !safety.attackGood(d.visibleLen))
            continue;
        if (!built) {
            field.build(w, w.head, P.huntMaxDist, -1, true);
            built = true;
        }
        int const dist = field.at(d.head);
        if (dist <= ownRamReach(w.length) || dist > P.huntMaxDist)
            continue;
        if (equal && ((prey && prey->visibleLen > d.visibleLen) ||
                      !ramWorthFor(w, safety, d.head, d.visibleLen, approachBody(w, field, d.head))))
            continue;
        bool const better = !prey || d.visibleLen > prey->visibleLen ||
                            (d.visibleLen == prey->visibleLen && (dist < preyDist || (dist == preyDist && d.id < prey->id)));
        if (better) {
            prey = &d;
            preyDist = dist;
        }
    }
    if (!prey)
        return -1;

    field.build(w, prey->head, preyDist, -1, true);
    int best = -1, bestDist = preyDist;
    bool bestEats = false;
    for (auto const& m : moves) {
        if (!m.safe)
            continue;
        int const dist = field.at(m.dest);
        bool const eats = w.hasPearlHere(m.dest);
        if (dist >= preyDist || (best >= 0 && std::tuple(-dist, eats, m.score) <=
                                                  std::tuple(-bestDist, bestEats, moves[best].score)))
            continue;
        best = m.dir;
        bestDist = dist;
        bestEats = eats;
    }
    return best;
}

} // namespace bot
