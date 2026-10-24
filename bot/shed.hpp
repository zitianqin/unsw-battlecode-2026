#pragma once

#include "helper.hpp"
#include "roomsplit.hpp"
#include "safety.hpp"
#include "scoring.hpp"
#include "state.hpp"

namespace bot {

// Shedding a trapped tail. A doomed dragon's split-out child (splitOutLength) is our rear reversed, so it starts
// at our tail. When our body bridges two closed pockets, as a long dragon in a 1-wide corridor between two
// dead-end farms does on Autarky and Dilemma, that child starts in the other pocket and is doomed there too: the
// dragon splits out at alternate ends forever, eating a pearl or two and losing two segments each time (about 250
// times a game there). So while our best move leads into a closed pocket and our tail is trapped, we split a
// P.childLength child off the rear instead of moving: our tail moves up until it reaches open ground, where a later
// split-out child can walk away, and the shed child farms the pocket behind us.
// With P.shedKnownTail, a tail we track is trapped when it has a free exit (the shed child must move) and none
// reaches P.trappedTailSpace tiles past walls and our own body (other dragons move on, so they never trap it). A long dragon rarely sees its tail, so with P.shedAtBirth,
// when it is out of sight we go by how we were born: a split-out child's tail lies in the pocket its parent was
// doomed in, beside that parent until it dies on its next turn, so a dragon born longer than P.childLength (a
// split-out child) sheds once, on its second turn, when that turn's best move is closed too. A starting dragon (first
// turn in round 0) has no parent beside its tail: with P.shedStarterAnyTurn it sheds on the first turn its tail is out
// of sight and its best move closed, from its first turn on and retried each turn until it has shed once (a long
// starter in a 1-wide column gets out in round 0, as the opponents' do), else only on its second turn as a split-out
// child does.
// Like an ordinary split, never with an enemy head within P.splitEnemyHeadRadius: the parent stands still. That
// radius counts plain torus distance, or with P.shedEnemyByPath steps through edges we know (kelp between us is no
// threat).
// With kNotes, notes.shedShadow names a shed the tail rules allow where either switch decides (debug only: its checks
// run on turns the enemy or timing rule already refused).
// With P.closedRoomSplits the shed child's room must hold both halves for the rounds left (roomsplit.hpp's
// roomHoldsSplit at `intakeRate`), judged from our head when the tail is out of sight.
// Returns the child length, 0 for none. shouldSplit (split.hpp) asks after the split-out and before ordinary splits.
inline int shedTrappedTail(World const& w, unswbc::Controller const& ct, Safety& safety, MoveEval const& best,
                           double intakeRate = P.capFeedTeamGrowth) {
    int const child = P.childLength;
    if (!best.closed || !ct.can_split(child))
        return 0;
    // An enemy head within the radius, by plain distance and by path.
    bool plainNear = false, pathNear = false;
    for (auto const& d : w.dragons)
        if (d.enemy && d.head >= 0) {
            plainNear = plainNear || w.torusDist(d.head, w.head) <= P.splitEnemyHeadRadius;
            pathNear = pathNear || w.pathWithin(w.head, d.head, P.splitEnemyHeadRadius);
        }
    if (plainNear && pathNear)
        return 0;
    bool const enemyHolds = P.shedEnemyByPath ? pathNear : plainNear;
    if (enemyHolds && !kNotes)
        return 0;
    auto const roomHolds = [&](int childHead) {
        if (roomHoldsSplit(w, safety, childHead, child, intakeRate))
            return true;
        if constexpr (kNotes)
            notes.roomHeld = "shed";
        return false;
    };
    // The shed the tail rules allow, and whether the starter timing alone allowed it.
    bool byStarterTiming = false;
    int const shed = [&] {
        if (static_cast<int>(w.body.size()) < w.length) {
            if (!P.shedAtBirth || w.bornLength <= child)
                return 0;
            bool const secondTurn = w.round == w.firstRound + 1;
            bool const starter = w.firstRound == 0 && w.shedRound < 0;
            if (!secondTurn && !starter)
                return 0;
            if (!roomHolds(w.head))
                return 0;
            byStarterTiming = !secondTurn;
            return child;
        }
        if (!P.shedKnownTail)
            return 0;
        int const tail = w.body.back();
        bool exit = false;
        for (int dir = 0; dir < 4; dir++) {
            int const to = w.step(tail, dir);
            if (to < 0 || w.occId[to] >= 0)
                continue;
            exit = true;
            FillOptions const childView{.ownBodyOnly = true, .physical = true};
            if (safety.floodFill(to, 1, P.trappedTailSpace, childView) >= P.trappedTailSpace)
                return 0;
        }
        return exit && roomHolds(tail) ? child : 0;
    }();
    bool const timingHolds = byStarterTiming && !P.shedStarterAnyTurn;
    if constexpr (kNotes) {
        if (shed && (plainNear != pathNear || byStarterTiming))
            notes.shedShadow = plainNear != pathNear && byStarterTiming ? "both" : byStarterTiming ? "starter" : "path";
    }
    return enemyHolds || timingHolds ? 0 : shed;
}

} // namespace bot
