#pragma once

#include "scoring.hpp"
#include "state.hpp"
#include "targets.hpp"

#include <array>

namespace bot {

// A refused pearl target. choosePearl ranks pearls by path cost and never asks whether evaluateMove will take the
// steps there, so a pearl whose approach carries a survival penalty (a closed pocket farmEntryOk turns down, jam.hpp's
// cut-off near the unit cap, a teammate's only exit or squeezing a teammate in (seal.hpp), an enemy's ram reach) stays the target while every other move
// is ordered by distance to it: the dragon hovers beside it, or beside the mouth of the pocket it lies in.

// The moves that bring us nearer the pearl target (by its path field) without being fatal now: a move onto a body
// that is still there or a head (below P.doomedScore) doesn't count, since the body moves on. Empty with no target.
inline std::array<bool, 4> approaches(Targets const& targets, std::array<MoveEval, 4> const& moves, int headDist) {
    std::array<bool, 4> near{};
    for (auto const& m : moves)
        near[m.dir] = m.dest >= 0 && m.terms[TERM_PENALTY] > P.doomedScore && targets.pearlField.at(m.dest) < headDist;
    return near;
}

// Whether move m carries no penalty beyond the out-of-sight term (P.unseenShortfallPerTile, at most
// P.visibleNeedCap tiles of it), which only orders moves and never turns a dragon off its target.
inline bool acceptable(MoveEval const& m) {
    return m.safe || m.terms[TERM_PENALTY] >= -P.unseenShortfallPerTile * P.visibleNeedCap;
}

// With P.dropRefusedTarget, true when the move scorer refused the pearl target this turn: it is choosePearl's own
// pick (not a fold's king or a feeder), some move approaches it (approaches()), none of those is acceptable(), the
// best-scored move is not one of them, and some move is safe. With P.refuseApproach at any distance; without it only
// when the target is one step away (the step onto it).
inline bool targetRefused(World const& w, Targets const& targets, std::array<MoveEval, 4> const& moves) {
    if (!P.dropRefusedTarget || targets.pearl < 0 || targets.pearl != targets.picked)
        return false;
    int const headDist = targets.pearlField.at(w.head);
    if (headDist < 1 || (headDist > 1 && !P.refuseApproach))
        return false;
    auto const near = approaches(targets, moves, headDist);
    bool anyNear = false, anySafe = false;
    for (auto const& m : moves) {
        if (near[m.dir] && acceptable(m))
            return false;
        anyNear = anyNear || near[m.dir];
        anySafe = anySafe || m.safe;
    }
    return anyNear && anySafe && !near[bestOf(moves, w.facing[w.head]).dir];
}

// Call after scoring the moves with this turn's targets. When the pearl target was refused (targetRefused), records
// the refusal, picks the target afresh (pickTarget: the room exit or a jammed room's exit, else the next pearl or an unknown portal beating
// it, else the race or an explore target) and returns true: main then redoes the fold and feeder retargets and scores
// the moves again, once. choosePearl skips the refused tile this turn, and when every approach entered a closed pocket
// farmEntryOk turned down for P.refusedPocketRounds rounds more, since that refusal outlasts the turn (the pearl count,
// our length and the unit cap it weighs change slowly)
// and a dragon circling beside it comes back every few rounds; a cut-off, a teammate's exit or way out, a ram risk or ground
// too small while bodies vacate it changes as the dragons move.
inline bool dropRefusedTarget(World const& w, Targets& targets, std::array<MoveEval, 4> const& moves) {
    if (!targetRefused(w, targets, moves))
        return false;
    auto const near = approaches(targets, moves, targets.pearlField.at(w.head));
    bool pocket = true;
    for (auto const& m : moves)
        if (near[m.dir])
            pocket = pocket && m.closed && !m.farm;
    targets.refuse(w, targets.pearl, pocket ? P.refusedPocketRounds : 0);
    targets.pickTarget(w);
    return true;
}

} // namespace bot
