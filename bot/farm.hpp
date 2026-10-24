#pragma once

#include "safety.hpp"
#include "state.hpp"

namespace bot {

// Dead-end farms. A closed pocket (see Safety::floodFill) is not certain death while the team has a free
// slot: a dragon of length >= 4 at its end can split everything but two segments off its tail, and the child
// (our rear reversed) walks back out. Only the length-2 parent is lost. So a pocket full of pearls or fast
// tiles is worth entering; evaluateMove asks farmEntryOk() before penalising a closed move (except for a king
// that keeps its length, fold.hpp's kingSkipsFarms), and splitOutLength() (split.hpp) splits once every move is fatal.

// True when the closed pocket behind `dest` is worth entering: nobody else is in it or at its mouth, and the
// pearls we can expect inside (visible pearls, empty fast tiles) repay the parent we leave behind and bring
// us to a length that can split out, with a free unit slot for that split-out (with P.farmHonorsReserve, one
// outside rescueReserve's, state.hpp). selfDelay is 1 when stepping onto dest eats.
inline bool farmEntryOk(World const& w, Safety& safety, int dest, int selfDelay) {
    if (w.unitCount >= w.unitLimit - (P.farmHonorsReserve ? rescueReserve(w) : 0))
        return false;
    int const cap = fillCap(w);
    safety.floodFill(dest, selfDelay, cap, {.physical = true});
    int pearls = 0;
    safety.queue.push_back(w.head); // the mouth, where our child will walk out
    for (int t : safety.queue) {
        if (t != w.head && (w.hasPearlHere(t) || (w.fastTile(t) && w.occId[t] < 0)))
            pearls++;
        if (w.occId[t] >= 0 && w.occId[t] != w.myId)
            return false;
        for (int dir = 0; dir < 4; dir++) {
            int const next = w.step(t, dir);
            if (next >= 0 && w.occId[next] >= 0 && w.occId[next] != w.myId)
                return false;
        }
    }
    return pearls >= P.farmMinPearls && w.length + pearls >= P.farmMinEndLength;
}

} // namespace bot
