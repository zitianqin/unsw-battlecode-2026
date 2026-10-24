#pragma once

#include "claims.hpp"
#include "params.hpp"
#include "state.hpp"

#include <algorithm>
#include <vector>

namespace bot {

// Contested ground (I1074): a tile the enemy reaches no later than we do, so food on it that we leave goes to them,
// while home food waits for us. With P.contestedDouble and a settled symmetry (World::symmetry), a tile t is contested
// when its path distance over known walls (unknown edges open, unknown portals closed: ground reached only through
// them is left out) from the mirror image of our birth tile, where the enemy's matching dragon started, is at most our
// own birth tile's distance plus P.contestedSlack. Targets counts the food of a contested pearl, spawn or drop tile
// P.contestedWorth times (choosePearl's yield) and an out-of-sight explore tile's staleness as much (bestStaleTile).
//
// "Our birth tile" is this process's (World::bornHead): a round-0 dragon's start, a split child's parent's old tail. A
// split child is a new process that knows nothing of the starters, so it reads the sides from where it was born:
// right for one born on our side, backwards for one born on the enemy's (8-10% of r0-100 births in 162 local games,
// 5-8% of those dragons' r0-100 turns). A dragon born
// within P.contestedSlack of its own mirror image (on the line) can't tell the sides apart, so the rule stays off for
// it.
//
// The king line counts contested food once (P.contestedKingOnce, I1101): the doubling is denial, a pearl the enemy
// doesn't eat, which the feeders can do, while the dragon whose length decides round 500 pays the walk and the ram risk
// of enemy ground for it. Main passes `king` for a dragon that believes it is king (fold.hpp's isKing: the seeding
// king, on big maps or a smaller one whose unit count has stalled, and from the beacon on every map). Call update()
// once a turn before targeting.
//
// Heads in numbers (P.bandNumbers, I1118): the band, contested ground both sides reach within P.contestedSlack steps
// of each other (band()), is a food and numbers fight, so food there counts `scale` times more, 2a / (a + b): our share
// of it, a / (a + b), when each head there eats alike, over the even share a half. a counts our heads on or heading to
// the band: this dragon, teammates in view with their head on it and the claimants of fresh pearl claims (claims.hpp)
// on it out of view. b counts the enemy heads in view on it while we see some of it (the fight in front of us; heads
// on the band's unseen stretches aren't counted); otherwise we have no count of theirs (the team belief keeps one bit
// a block, no counts), and the mirror, their dragons playing as ours do, gives them ours (b = a, scale 1). So our
// numbers move the worth only against a count we see: a dragon walking in alone against three seen there counts that
// food at half its worth, still no refusal, with two teammates there too it counts it at 2 x 3 / (3 + 3), full worth,
// and one that sees its stretch of the band empty counts it double. The enemy's side of contested ground keeps
// P.contestedWorth.
struct Contested {
    std::vector<int> ours, theirs, queue;
    bool on = false;
    double scale = 1;   // band food's worth multiplier this turn (heads in numbers)
    int heads = 1;      // ... from a, our heads on or heading to the band ...
    int enemyHeads = 1; // ... and b, theirs (a when we see none of the ground)
    std::vector<int> ids; // scratch: claimant ids counted this turn

    bool at(int t) const { return on && theirs[t] <= ours[t] + P.contestedSlack; }
    bool band(int t) const { return at(t) && ours[t] <= theirs[t] + P.contestedSlack; }

    void update(World const& w, bool king = false) {
        on = false;
        if (!P.contestedDouble || (king && P.contestedKingOnce) || w.symmetry < 0 || w.bornHead < 0)
            return;
        int const enemy = w.mirrorTile(w.bornHead, w.symmetry);
        fill(w, w.bornHead, ours);
        fill(w, enemy, theirs);
        on = true;
        on = !at(w.bornHead);
    }

    // With P.bandNumbers and the ground on: counts a and b and sets `scale` (see the top of this file); 1 otherwise.
    // Call after update(), with the claims this dragon heard (nullptr: teammates in view only).
    void count(World const& w, Claims const* claims) {
        scale = 1;
        heads = enemyHeads = 1;
        if (!P.bandNumbers || !on)
            return;
        ids.clear();
        ids.push_back(w.myId & PearlClaim::ID_MASK);
        int enemies = 0;
        for (auto const& d : w.dragons) {
            if (d.id == w.myId || d.head < 0 || !band(d.head))
                continue;
            if (d.enemy)
                enemies++;
            else
                ids.push_back(d.id & PearlClaim::ID_MASK);
        }
        if (claims && P.pearlClaims && !claims->eat.empty())
            for (int t = 0; t < w.N; t++)
                if (claims->eat[t] >= w.round && band(t) &&
                    std::find(ids.begin(), ids.end(), claims->by[t]) == ids.end())
                    ids.push_back(claims->by[t]);
        heads = static_cast<int>(ids.size());
        bool seeGround = false;
        for (int t = 0; t < w.N && !seeGround; t++)
            seeGround = w.visible(t) && band(t);
        enemyHeads = seeGround ? enemies : heads;
        scale = 2.0 * heads / (heads + enemyHeads);
    }

private:
    // Path steps from src to every tile over known edges (World::step: bodies ignored), a large value where none.
    void fill(World const& w, int src, std::vector<int>& dist) {
        dist.assign(w.N, w.N);
        queue.resize(w.N);
        std::size_t tail = 0;
        queue[tail++] = src;
        dist[src] = 0;
        for (std::size_t at = 0; at < tail; at++) {
            int const cur = queue[at];
            for (int dir = 0; dir < 4; dir++) {
                int const to = w.step(cur, dir);
                if (to < 0 || dist[to] <= dist[cur] + 1)
                    continue;
                dist[to] = dist[cur] + 1;
                queue[tail++] = to;
            }
        }
    }
};

} // namespace bot
