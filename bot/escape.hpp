#pragma once

#include "enemy_reach.hpp"
#include "hunt.hpp"
#include "safety.hpp"
#include "state.hpp"

#include <algorithm>
#include <utility>
#include <vector>

namespace bot {

// Steps (dir indices) of an escape sprint; empty: none.
struct Escape {
    std::vector<int> steps;
    int pearls = 0; // pearls eaten on the way
    int room = 0;   // physical flood fill from the end tile
};

// True when enemy head `from` reaches `end` within `reach` steps over tiles free after our sprint: empty tiles
// (out of sight counts as empty) and our tail tiles the sprint vacated (tail distance < vacated); `mid`, our
// new neck, and every other body blocks.
inline bool enemyReaches(World const& w, int from, int end, int mid, int vacated, int reach) {
    std::vector<std::pair<int, int>> queue{{from, 0}};
    for (std::size_t at = 0; at < queue.size(); at++) {
        auto const [cur, depth] = queue[at];
        if (depth >= reach)
            continue;
        for (int dir = 0; dir < 4; dir++) {
            int const to = w.step(cur, dir);
            if (to == end)
                return true;
            if (to < 0 || to == mid)
                continue;
            if (w.occId[to] >= 0 && !(w.occId[to] == w.myId && w.occK[to] >= 0 && w.occK[to] < vacated))
                continue;
            bool seen = false;
            for (auto const& [t, d] : queue)
                seen = seen || t == to;
            if (!seen)
                queue.push_back({to, depth + 1});
        }
    }
    return false;
}

// A one-step ram we can't dodge by stepping: a long dragon whose every non-lethal single step is next to an enemy
// head that would be a bad trade for us (Safety::riskyFor) dies there one move late whatever it plays (most of the
// head-ons our long dragons suffer from shorter enemies). A 2-step sprint moves two tiles for one tail segment, so
// it can get beyond their reach. Searches every 2-step sprint over visible empty tiles whose end no such head (a bad
// trade for us there: Safety::badRammer) reaches within its sprint reach (fullEnemyReaches with P.fullEnemyReach,
// else the legacy capped fill) and whose physical flood fill passes evaluateMove's room test (open, and at least our
// new length + P.spaceMargin tiles); returns the one eating the most pearls, then keeping the most room, then first
// in N, E, S, W order. Empty when a single step is safe (not lethal, not risky),
// no step is risky, or no sprint qualifies. With P.escapeSprint, main() plays it right after findRam.
inline Escape findEscape(World const& w, Safety& safety, bool full = P.fullEnemyReach) {
    Escape best;
    if (!P.escapeSprint || w.length < P.escapeMinLength)
        return best;
    int risky = 0;
    for (int dir = 0; dir < 4; dir++) {
        int const to = w.step(w.head, dir);
        if (to == UNKNOWN_PORTAL)
            return best;
        if (to < 0 || w.occHead[to] || w.freeAt(to, 0) > 1)
            continue;
        if (!safety.riskyFor(to))
            return best;
        risky++;
    }
    if (risky == 0)
        return best;

    std::vector<SeenDragon const*> enemies;
    for (auto const& d : w.dragons)
        if (d.enemy)
            enemies.push_back(&d);
    int const cap = fillCap(w), hold = holdCap(w);
    auto const open = [&](int t) { return t >= 0 && w.visible(t) && w.occId[t] < 0; };
    for (int first = 0; first < 4; first++) {
        int const mid = w.step(w.head, first);
        if (!open(mid))
            continue;
        for (int second = 0; second < 4; second++) {
            int const end = w.step(mid, second);
            if (!open(end) || end == w.head)
                continue;
            int const pearls = (w.hasPearlHere(mid) ? 1 : 0) + (w.hasPearlHere(end) ? 1 : 0);
            int const vacated = 3 - pearls;
            bool reached = false;
            for (auto const* d : enemies)
                reached = reached || (safety.badRammer(*d, {first, second}) &&
                                      (full ? fullEnemyReaches(w, *d, end, enemyReach(w, *d, true), mid, vacated)
                                            : enemyReaches(w, d->head, end, mid, vacated, enemyReach(w, *d, false))));
            if (reached)
                continue;
            int const need = std::min(w.length - 1 + pearls + P.spaceMargin, hold);
            int const room = safety.floodFill(end, pearls - 2, cap, {.physical = true, .wall = mid});
            if ((room < hold && !safety.reachedFreed) || room < need)
                continue;
            if (best.steps.empty() || pearls > best.pearls || (pearls == best.pearls && room > best.room))
                best = {{first, second}, pearls, room};
        }
    }
    return best;
}

} // namespace bot
