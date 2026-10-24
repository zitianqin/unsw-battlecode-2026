#pragma once

#include "safety.hpp"
#include "state.hpp"

namespace bot {

// Swarm jams at the unit cap. Every other dragon moves once between this move and our next, so a region whose way
// out runs through a tile another head can step onto may be sealed before we get there: at the cap our saturated
// swarm walls its own dragons in this way (a teammate's head takes the mouth of the pocket we just entered), and a
// trapped dragon can split its rear out only into a free slot (the few that state.hpp's rescueReserve keeps from
// ordinary splits, on big maps unless P.shedReserve asks for more), losing its front even then. evaluateMove asks this for a move that is
// physically open and big enough; evaluateMoves then penalises such a move only when another move stays open.

// True when P.headReachGuard applies (within P.headReachCapMargin dragons of the unit cap) and the region past
// `dest`, refilled with every tile another visible head can step onto as a wall (FillOptions::headReachBlocks), is
// closed or smaller than `need`.
inline bool cutOffByHeads(World const& w, Safety& safety, int dest, int selfDelay, int cap, int need) {
    if (!P.headReachGuard || w.unitCount < w.unitLimit - P.headReachCapMargin)
        return false;
    int const room = safety.floodFill(dest, selfDelay, cap, {.physical = true, .headReachBlocks = true});
    return (room < cap && !safety.reachedFreed) || room < need;
}

} // namespace bot
