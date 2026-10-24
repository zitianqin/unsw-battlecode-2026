#pragma once

#include "enemy_reach.hpp"
#include "params.hpp"
#include "state.hpp"

#include <algorithm>
#include <deque>
#include <vector>

namespace bot {

// Steps for the nearest of one team's other visible heads to reach each tile, out to P.harvestReach: the heads that
// will race for a ram's drop. Our head and the victim's (`victim`, or -1 for none: a feed) are left out (both die),
// and so are their bodies as walls: the drop lies where they were. Other tiles are walked as DistField walks them (a
// tile that never frees is reached but not walked through). With `landings` (a head's one-turn sprint, sprintLandings),
// those tiles are reached in one turn and walked on from there.
struct HarvestField {
    std::vector<int> dist, mark, queue;
    int stamp = 0;

    int at(int t) const { return mark[t] == stamp ? dist[t] : NEVER; }

    void build(World const& w, bool enemies, int victim, std::vector<int> const& landings = {}) {
        if (static_cast<int>(dist.size()) != w.N) {
            dist.assign(w.N, 0);
            mark.assign(w.N, 0);
            queue.assign(w.N, 0);
        }
        stamp++;
        int const victimId = victim >= 0 ? w.occId[victim] : -1;
        std::size_t tail = 0;
        for (auto const& d : w.dragons) {
            if (d.head < 0 || d.id == w.myId || d.head == victim || d.enemy != enemies || mark[d.head] == stamp)
                continue;
            mark[d.head] = stamp;
            dist[d.head] = 0;
            queue[tail++] = d.head;
        }
        for (int t : landings) {
            if (mark[t] == stamp)
                continue;
            mark[t] = stamp;
            dist[t] = 1;
            queue[tail++] = t;
        }
        std::size_t const seeds = tail;
        for (std::size_t at = 0; at < tail; at++) {
            int const cur = queue[at];
            if (dist[cur] >= P.harvestReach)
                continue;
            int const who = w.occId[cur];
            if (at >= seeds && who != w.myId && who != victimId && w.freeAt(cur, 0) >= NEVER)
                continue;
            for (int dir = 0; dir < 4; dir++) {
                int const to = w.step(cur, dir);
                if (to < 0 || mark[to] == stamp)
                    continue;
                mark[to] = stamp;
                dist[to] = dist[cur] + 1;
                queue[tail++] = to;
            }
        }
    }
};

// Our body (head first) as it lies when a ram along `steps` (ending on an enemy head) collides: the fatal last step
// never moves us, so our head stops one tile short; each step after the first sheds a tail segment, and a pearl eaten
// on the way pays one back.
inline std::vector<int> ramBody(World const& w, std::vector<int> const& steps) {
    std::vector<int> body;
    int length = w.length;
    int at = w.head;
    for (std::size_t j = 0; j + 1 < steps.size(); j++) {
        at = w.step(at, steps[j]);
        body.push_back(at);
        length += (w.hasPearlHere(at) ? 1 : 0) - (j >= 1 ? 1 : 0);
    }
    std::reverse(body.begin(), body.end());
    body.insert(body.end(), w.body.begin(), w.body.end());
    body.resize(std::min<std::size_t>(body.size(), std::max(length, 1)));
    return body;
}

// The tiles a head-on of our `body` (head first, as ramBody or the hunt's approach lays it) with the enemy head at
// `victim` leaves pearls on: every second segment from the head of the victim's visible chain, and of our body.
inline std::vector<int> dropTiles(World const& w, int victim, std::vector<int> const& body) {
    std::vector<int> drops;
    SeenDragon const* them = w.dragonAt(victim);
    int const theirLen = them ? them->visibleLen : 1;
    int k = 0;
    for (int cur = victim; cur >= 0 && k < theirLen; cur = w.behind[cur], k++)
        if (k % 2 == 0)
            drops.push_back(cur);
    for (std::size_t i = 0; i < body.size(); i += 2)
        drops.push_back(body[i]);
    return drops;
}

// The tiles a ram along `steps` (ending on the enemy head at `victim`) leaves pearls on.
inline std::vector<int> ramDropTiles(World const& w, int victim, std::vector<int> const& steps) {
    return dropTiles(w, victim, ramBody(w, steps));
}

// The learned ram model's expected swing (P.ramW*, E0204; rammodel.hpp's ramSwing) of a head-on for the side that
// starts it: a head of length `rammerLen` at `rammer` moves into one of length `victimLen` on tile `victim`. `edge` is
// the rammer's side's visible heads less the other side's, and the other visible heads within P.dropEdgeRadius of
// `victim` count for the side they belong to (who eats the drop); our head, the rammer and the victim tile are left
// out. `enemyRams`: an enemy head rams us (the rammer's friends are enemy heads), else we ram.
inline double headOnSwing(World const& w, bool enemyRams, int rammer, int rammerLen, int victim, int victimLen,
                          int edge) {
    int nearFriends = 0, nearEnemies = 0;
    for (auto const& d : w.dragons) {
        if (d.head < 0 || d.head == w.head || d.head == rammer || d.head == victim ||
            w.torusDist(d.head, victim) > P.dropEdgeRadius)
            continue;
        (d.enemy == enemyRams ? nearFriends : nearEnemies)++;
    }
    return P.ramWBias + P.ramWGain * (victimLen - rammerLen) + P.ramWEdge * edge + P.ramWNearFriends * nearFriends +
           P.ramWNearEnemies * nearEnemies;
}

// Who eats a ram's drop (P.evenTradeHarvest): the drop tiles (dropTiles, our `body` as it lies at the collision) our
// nearest other head reaches strictly before every other enemy head, less those an enemy head reaches strictly first,
// each within P.harvestReach steps.
inline int dropHarvestEdgeFor(World const& w, int victim, std::vector<int> const& body) {
    static HarvestField ours, theirs;
    ours.build(w, false, victim);
    theirs.build(w, true, victim);
    int edge = 0;
    for (int t : dropTiles(w, victim, body)) {
        int const a = ours.at(t), b = theirs.at(t);
        edge += a < b ? 1 : b < a ? -1 : 0;
    }
    return edge;
}

// dropHarvestEdgeFor a ram along `steps` (ramBody).
inline int dropHarvestEdge(World const& w, int victim, std::vector<int> const& steps) {
    return dropHarvestEdgeFor(w, victim, ramBody(w, steps));
}

// The free tiles enemy head d lands on this turn with one sprint of up to `reach` steps (enemyReach), our own body
// counted free (we die) and `pearls` (our drop) paying as the visible pearls do: fullEnemyReaches's 0-1 fill of
// unpaid steps, each tile stepped onto from one labelled at most reach - 1 a landing. Every other body blocks.
inline void sprintLandings(World const& w, SeenDragon const& d, int reach, std::vector<char> const& pearls,
                           std::vector<int>& landings) {
    static std::vector<int> cost;
    static std::vector<char> landed;
    cost.assign(w.N, DistField::FAR);
    landed.assign(w.N, 0);
    std::deque<int> queue{d.head};
    cost[d.head] = 0;
    while (!queue.empty()) {
        int const cur = queue.front();
        queue.pop_front();
        if (cost[cur] > reach - 1)
            continue;
        for (int dir = 0; dir < 4; ++dir) {
            int const to = w.step(cur, dir);
            if (to < 0 || to == d.head || (w.occId[to] >= 0 && w.occId[to] != w.myId))
                continue;
            if (!landed[to]) {
                landed[to] = 1;
                landings.push_back(to);
            }
            int const next = cost[cur] + (w.hasPearlHere(to) || pearls[to] ? 0 : 1);
            if (next >= cost[to])
                continue;
            cost[to] = next;
            if (next > cost[cur])
                queue.push_back(to);
            else
                queue.push_front(to);
        }
    }
}

// Who eats a folding dragon's own drop (P.foldFeedByDrop): its drop tiles (our head and every second segment of the
// body we track) our other visible heads reach strictly before every visible enemy head, less those an enemy head
// reaches strictly first, each within P.harvestReach turns. Our heads step; an enemy head also lands in one turn on
// any tile its paid sprint reaches (sprintLandings) and walks on from there.
inline int feedDropEdge(World const& w) {
    static HarvestField ours, theirs;
    static std::vector<char> pearls;
    std::vector<int> drops, landings;
    pearls.assign(w.N, 0);
    int const known = std::min(w.length, static_cast<int>(w.body.size()));
    for (int i = 0; i < known; i += 2) {
        drops.push_back(w.body[i]);
        pearls[w.body[i]] = 1;
    }
    for (auto const& d : w.dragons)
        if (d.enemy && d.head >= 0)
            sprintLandings(w, d, enemyReach(w, d), pearls, landings);
    ours.build(w, false, -1);
    theirs.build(w, true, -1, landings);
    int edge = 0;
    for (int t : drops) {
        int const a = ours.at(t), b = theirs.at(t);
        edge += a < b ? 1 : b < a ? -1 : 0;
    }
    return edge;
}

} // namespace bot
