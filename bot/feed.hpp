#pragma once

#include "harvest.hpp"
#include "notes.hpp"
#include "safety.hpp"
#include "state.hpp"
#include "targets.hpp"

#include <algorithm>
#include <cmath>

namespace bot {

// The merge cascade: the round-500 tiebreak is our longest living dragon, and a swarm of short dragons never
// grows one. A dead dragon drops ceil(L/2) pearls where it stands (head first, then every second segment), so
// a dragon beside a teammate at least as long converts its length into the teammate's; repeated pairwise,
// the swarm folds into one king (state.hpp's mergesInto says who merges into whom).

// A dragon in the final fold (fold.hpp): it walks to the king `kingId`, reported `kingLen` long, to merge into it.
struct Fold {
    bool active = false;
    int kingId = -1;
    int kingLen = 0;
};

// True when a folding dragon merges past the unit floor: with P.foldIgnoresMinUnits always, and with P.floorByTime
// once the rest of the team (w.unitCount - 1) outnumbers what the fastest measured wipe-out, scaled to this map's area,
// could eliminate in the rounds left (params.hpp's wipeRate formula). foldTowardKing and feedDirection share it.
inline bool foldPastFloor(World const& w) {
    if (P.foldIgnoresMinUnits)
        return true;
    if (!P.floorByTime)
        return false;
    int const roundsLeft = unswbc::Constants::MAX_ROUNDS - 1 - w.round;
    double const rate = P.wipeRate * std::pow(double(P.wipeRefArea) / w.N, P.wipeAreaPower);
    return w.unitCount - 1 > rate * roundsLeft * P.wipeSafety;
}

// Returns the direction to die by (kelp or our own body, never a head), or -1 to play normally. Fires from
// P.mergeRound while the team has more than P.mergeMinUnits dragons and no enemy head is in view, when a
// teammate we merge into (mergesInto) reaches our head tile within P.mergeRadius steps, strictly before every
// other visible head, so its Targets claim the drop. `field` is scratch space for the path lengths.
// A folding dragon (`fold`, active only from state.hpp's foldStart) may merge into the king, within P.foldMergeRadius steps, counting
// it as long as it was reported; with P.foldMergesOnlyKing any other teammate takes it only as in the cascade
// (from P.mergeRound, within P.mergeRadius), so the fold's mass isn't halved again by merges on the way.
// foldPastFloor / P.foldIgnoresEnemy skip the unit-count / enemy-in-view gates for a folding dragon. With
// P.foldFeedByDrop, when enemy heads in view are all that holds a folding dragon's merge, it merges if our other heads
// win the race for its drop (feedDropEdge above zero), as the ram gates price a head-on's drop.
// With kNotes it leaves in notes.feedWhy "merge" (or "dropwin" when the drop race let it through) or the gate that
// returned -1: early (before the round it starts), floor (P.mergeMinUnits), enemy (a head in view; for a folder under
// P.foldFeedByDrop, the drop race lost), far (no teammate to merge into within its radius), notfirst (another head
// reaches ours as soon) or nodeath (no kelp or own-body step to die by); once it measures paths, notes.feedMate,
// feedMateDist and feedOthersDist hold the teammate it would merge into and the two path distances it compares.
inline int feedDirection(World const& w, DistField& field, Fold const& fold = {}) {
    int const from = fold.active ? 0 : P.mergeRound;
    if (w.round < from || (w.unitCount <= P.mergeMinUnits && !(fold.active && foldPastFloor(w)))) {
        if constexpr (kNotes)
            notes.feedWhy = w.round < from ? "early" : "floor";
        return -1;
    }
    // How far away teammate d may be for us to merge into it, or -1 when we don't merge into it.
    auto const mateRadius = [&](SeenDragon const& d) {
        if (d.enemy || d.id == w.myId)
            return -1;
        bool const king = fold.active && d.id == fold.kingId;
        int const len = king ? std::max(d.visibleLen, fold.kingLen) : d.visibleLen;
        if (!mergesInto(w.length, w.myId, len, d.id))
            return -1;
        if (king || (fold.active && !P.foldMergesOnlyKing))
            return P.foldMergeRadius;
        return w.round >= P.mergeRound ? P.mergeRadius : -1;
    };
    int radius = -1;
    bool enemyInView = false;
    for (auto const& d : w.dragons) {
        if (d.enemy) {
            if (fold.active && (P.foldIgnoresEnemy || P.foldFeedByDrop)) {
                enemyInView = true;
                continue;
            }
            if constexpr (kNotes)
                notes.feedWhy = "enemy";
            return -1;
        }
        if (int const r = mateRadius(d); r >= 0 && w.torusDist(d.head, w.head) <= r)
            radius = std::max(radius, r);
    }
    if (radius < 0) {
        if constexpr (kNotes)
            notes.feedWhy = "far";
        return -1;
    }

    field.build(w, w.head, radius + 1);
    int mateDist = DistField::FAR, mateReach = -1, othersDist = DistField::FAR;
    for (auto const& d : w.dragons) {
        if (d.id == w.myId)
            continue;
        int const dist = field.at(d.head);
        if (int const r = mateRadius(d); r >= 0 && dist < mateDist) {
            othersDist = std::min(othersDist, mateDist);
            mateDist = dist;
            mateReach = r;
            if constexpr (kNotes)
                notes.feedMate = d.id;
        } else {
            othersDist = std::min(othersDist, dist);
        }
    }
    if constexpr (kNotes) {
        notes.feedMateDist = mateDist;
        notes.feedOthersDist = othersDist;
    }
    if (mateDist > mateReach || mateDist >= othersDist) {
        if constexpr (kNotes)
            notes.feedWhy = mateDist > mateReach ? "far" : "notfirst";
        return -1;
    }

    for (int dir = 0; dir < 4; dir++) {
        int const to = w.step(w.head, dir);
        if (to == KELP || (to >= 0 && w.occId[to] == w.myId && !w.occHead[to])) {
            bool const raced = enemyInView && !P.foldIgnoresEnemy;
            if (raced && feedDropEdge(w) <= 0) {
                if constexpr (kNotes)
                    notes.feedWhy = "enemy";
                return -1;
            }
            if constexpr (kNotes)
                notes.feedWhy = raced ? "dropwin" : "merge";
            return dir;
        }
    }
    if constexpr (kNotes)
        notes.feedWhy = "nodeath";
    return -1;
}

// With P.kingSeeksFeeders, a king whose pearl target is not eaten within P.kingPearlReach steps retargets the nearest
// visible teammate head that would merge into it (at its visible length, lower id on a tie), so feeders come within
// merging range. A king is Safety::protectedDragon (from P.mergeRound) or, with `foldKing` (fold.hpp's
// kingSeeksInFold: a dragon that believes it is king from the fold's start), one whose folders merge into it from
// then. Only with more than P.mergeMinUnits dragons (a fold king also at or below that floor while folders merge
// past it: foldPastFloor) and no enemy head in view. Call after Targets::update; returns true when it retargeted. `field` is
// scratch space.
inline bool seekFeeder(World const& w, Safety const& safety, Targets& targets, DistField& field, bool foldKing = false) {
    if (!P.kingSeeksFeeders || !(safety.protectedDragon || foldKing) ||
        (w.unitCount <= P.mergeMinUnits && !(foldKing && foldPastFloor(w))))
        return false;
    if (targets.pearl >= 0 && std::max(targets.pearlField.at(w.head), targets.pearlReady) <= P.kingPearlReach)
        return false;
    for (auto const& d : w.dragons)
        if (d.enemy)
            return false;

    field.build(w, w.head, P.maxTargetDist);
    SeenDragon const* feeder = nullptr;
    int feederDist = DistField::FAR;
    for (auto const& d : w.dragons) {
        if (d.id == w.myId || !mergesInto(d.visibleLen, d.id, w.length, w.myId))
            continue;
        int const dist = field.at(d.head);
        if (dist < feederDist || (dist == feederDist && feeder && d.id < feeder->id)) {
            feeder = &d;
            feederDist = dist;
        }
    }
    if (!feeder || feederDist > P.maxTargetDist)
        return false;
    targets.pearl = feeder->head;
    targets.pearlReady = 0;
    targets.pearlField.build(w, feeder->head, P.maxTargetDist + 1, w.head);
    return true;
}

} // namespace bot
