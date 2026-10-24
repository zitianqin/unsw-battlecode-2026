#pragma once

#include "state.hpp"

#include <algorithm>
#include <deque>
#include <vector>

namespace bot {

// Steps enemy head d can sprint before pearls: its visible length minus one when its visible chain ends in a tail we
// can confirm (World::interior), else at least P.maxRamSteps, since visibleLen is only a lower bound when the body
// runs on out of sight (`unseenTail` false drops that fallback and trusts visibleLen). With `full` off (the legacy
// model) the reach is also capped at P.maxRamSteps.
inline int enemyReach(World const& w, SeenDragon const& d, bool full = P.fullEnemyReach, bool unseenTail = true) {
    int last = d.head;
    for (int k = 1; k < d.visibleLen && w.behind[last] >= 0; k++)
        last = w.behind[last];
    int const known = std::max(1, d.visibleLen - 1);
    bool const confirmed = !unseenTail || w.interior(last);
    if (full)
        return confirmed ? known : std::max(known, P.maxRamSteps);
    return confirmed ? std::min(P.maxRamSteps, known) : P.maxRamSteps;
}

// True when enemy head d can ram tile `end` this turn with a sprint of up to `reach` unpaid steps (enemyReach, plus
// any slack), every pearl it eats on the way paying for one more (P.fullEnemyReach): its first step is free, each
// later one costs a segment, and a pearl eaten on a step before the last pays one back, so a 0-1 fill labels each
// free tile with the fewest unpaid steps to it and the ram lands from any tile labelled at most reach - 1. A pearl on
// `end` pays nothing: the step onto it is the head-on. Known portals count as one step (World::step). findEscape's
// calls allow unseen ground and our tail tiles the escape vacated (tail distance < `vacated`) and block our new neck
// `mid`; shedLength's (`visibleOnly`) expand visible tiles only. Every other body blocks.
inline bool fullEnemyReaches(World const& w, SeenDragon const& d, int end, int reach, int mid = -1, int vacated = 0,
                             bool visibleOnly = false) {
    int const budget = reach - 1;
    std::vector<int> cost(w.N, DistField::FAR);
    std::deque<int> queue{d.head};
    cost[d.head] = 0;
    while (!queue.empty()) {
        int const cur = queue.front();
        queue.pop_front();
        if (cost[cur] > budget)
            continue;
        for (int dir = 0; dir < 4; ++dir) {
            int const to = w.step(cur, dir);
            if (to == end)
                return true;
            if (to < 0 || to == mid || to == d.head || (visibleOnly && !w.visible(to)))
                continue;
            if (w.occId[to] >= 0 && !(w.occId[to] == w.myId && w.occK[to] >= 0 && w.occK[to] < vacated))
                continue;
            int const spent = w.hasPearlHere(to) ? 0 : 1;
            int const next = cost[cur] + spent;
            if (next > budget || next >= cost[to])
                continue;
            cost[to] = next;
            if (spent)
                queue.push_back(to);
            else
                queue.push_front(to);
        }
    }
    return false;
}

} // namespace bot
