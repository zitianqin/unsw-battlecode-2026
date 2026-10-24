#pragma once

#include "safety.hpp"
#include "state.hpp"

#include <array>
#include <vector>

namespace bot {

// The squeeze guard (P.guardTeammateSqueeze). A step of ours can take a visible teammate's last way out without
// touching its next step (Safety::blocksTeammateExit sees only that): mostly our bodies squeezing it in crowded ground
// (E0255: 70% of the changed moves at the unit cap, 0.03% with the teammate in a map pocket), sometimes a pocket's
// mouth. So for each visible teammate this judges its own timed flood fill, run from its head as it would run it on its
// next turn, with our body where the step leaves it: every body tile enters at the step its tail distance frees it
// (ours from our tracked body, the teammate's own held back one step per pearl on its path, the others' as
// World::freeAt has them), and the fill is open when it counts the teammate's fill cap (with P.roomByRoundsLeft at
// most the moves the game has left, as holdCap) or enters a tile that frees in time (it can follow a tail out), as
// Safety::floodFill judges our own. As there, a portal we have never seen through is no way out: rooms only portals
// lead out of hold several, and counting them left every teammate inside one open.
// evaluateMoves charges P.squeezePenalty to a step after which some teammate's fill is closed while another step of
// ours with no survival penalty of its own keeps it open (squeezeCharged: with P.squeezeOnValue a step that eats pays
// only while the squeezed teammate stands to lose more than the pearl).

// The step of `teammate`'s fill at which tile t can be entered, after our step onto `dest` (eating when `eats`; -1:
// every tile beside our head blocked and our body held as if we ate, the most any step of ours can close). `delay`
// is the pearls on the teammate's path so far, which hold its own tail back.
inline int sealFreeAt(World const& w, SeenDragon const& teammate, int dest, bool eats, int t, int delay) {
    if (dest < 0) {
        for (int dir = 0; dir < 4; dir++)
            if (w.step(w.head, dir) == t)
                return NEVER;
    } else if (t == dest) {
        return w.length + (eats ? 1 : 0) + 1 + P.otherTailMargin;
    }
    int const id = w.occId[t];
    if (id < 0)
        return 0;
    if (id == w.myId) {
        if (w.occK[t] < 0)
            return NEVER;
        int const k = w.occK[t] - (dest >= 0 && !eats ? 1 : 0);
        return k < 0 ? 0 : k + 2 + P.otherTailMargin;
    }
    if (id == teammate.id)
        return w.occHead[t] || w.occK[t] < 0 ? NEVER : w.occK[t] + 2 + delay;
    return w.freeAt(t, 0);
}

// True when `teammate`'s timed fill (see above) after our step onto `dest` is open. Uses `safety`'s scratch.
inline bool teammateFillOpen(World const& w, Safety& safety, SeenDragon const& teammate, int dest, bool eats) {
    int cap = std::max(P.spaceCap, P.spaceCapPerLength * teammate.visibleLen);
    if (P.roomByRoundsLeft)
        cap = std::min(cap, unswbc::Constants::MAX_ROUNDS - w.round);
    safety.seenStamp++;
    auto& seen = safety.seen;
    auto& depth = safety.depth;
    auto& delay = safety.delay;
    auto& queue = safety.queue;
    queue.clear();
    queue.push_back(teammate.head);
    seen[teammate.head] = safety.seenStamp;
    depth[teammate.head] = 0;
    delay[teammate.head] = 0;
    int count = 0;
    for (std::size_t at = 0; at < queue.size(); at++) {
        int const cur = queue[at];
        int const next = depth[cur] + 1;
        for (int dir = 0; dir < 4; dir++) {
            int const to = w.step(cur, dir);
            if (to < 0 || seen[to] == safety.seenStamp)
                continue;
            int const free = sealFreeAt(w, teammate, dest, eats, to, delay[cur]);
            if (free > next)
                continue;
            if (free > 0)
                return true;
            seen[to] = safety.seenStamp;
            depth[to] = next;
            delay[to] = delay[cur] + (w.hasPearlHere(to) ? 1 : 0);
            queue.push_back(to);
            if (++count >= cap)
                return true;
        }
    }
    return false;
}

// For each of our four steps, the visible length of the longest teammate it squeezes in (0 none): that teammate's
// fill is closed after it while another step in `keeps` leaves it open. `candidate[dir]` marks the steps that are real
// moves onto a tile (not kelp, a portal we don't know, a head or a body still there), `keeps[dir]` those of them with no
// survival penalty of their own (MoveEval::survives), so our only safe step is never charged. A teammate whose fill
// stays open with every tile beside our head blocked is left alone: no step of ours can close it.
inline std::array<int, 4> sealsTeammate(World const& w, Safety& safety, std::array<bool, 4> const& candidate,
                                        std::array<bool, 4> const& keeps) {
    std::array<int, 4> seals{};
    if (!P.guardTeammateSqueeze)
        return seals;
    for (auto const& d : w.dragons) {
        if (d.enemy || d.id == w.myId || d.head < 0)
            continue;
        if (teammateFillOpen(w, safety, d, -1, true))
            continue;
        std::array<bool, 4> open{};
        for (int dir = 0; dir < 4; dir++) {
            if (!candidate[dir])
                continue;
            int const dest = w.step(w.head, dir);
            open[dir] = teammateFillOpen(w, safety, d, dest, w.hasPearlHere(dest));
        }
        for (int dir = 0; dir < 4; dir++) {
            bool kept = false;
            for (int other = 0; other < 4; other++)
                kept = kept || (other != dir && keeps[other] && open[other]);
            if (candidate[dir] && !open[dir] && kept)
                seals[dir] = std::max(seals[dir], d.visibleLen);
        }
    }
    return seals;
}

// Whether a step that squeezes in a teammate `sealedLen` long (sealsTeammate) pays P.squeezePenalty: always, or with
// P.squeezeOnValue, when the step eats, only while the teammate's expected loss (P.squeezeDeathChance, the guard's
// measured cut in squeezed teammates' deaths, times its visible length) is more than the pearl the step eats.
inline bool squeezeCharged(int sealedLen, bool eats) {
    if (sealedLen <= 0)
        return false;
    return !P.squeezeOnValue || !eats || P.squeezeDeathChance * sealedLen > 1;
}

} // namespace bot
