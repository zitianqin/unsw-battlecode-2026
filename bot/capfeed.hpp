#pragma once

#include "belief.hpp"
#include "fold.hpp"
#include "notes.hpp"
#include "state.hpp"
#include "targets.hpp"

#include <algorithm>
#include <cmath>
#include <numeric>
#include <string_view>
#include <utility>
#include <vector>

namespace bot {

// What this process has eaten, turn by turn: its length's change since its last turn plus what it knowingly spent
// (a split child, a sprint's paid steps), so a split-off isn't read as a fast; and the team's unit count on each turn,
// for how fast the team fills free slots (unitGrowth). Call observe() once a turn after World::observe and spent()
// when a turn gives length away.
struct Intake {
    std::vector<int> eaten; // pearls eaten on each turn since our second one
    std::vector<std::pair<int, int>> units; // (round, the team's unit count) on each of our turns
    int prevLength = -1;
    int lost = 0;

    void observe(World const& w) {
        if (prevLength >= 0)
            eaten.push_back(std::max(0, w.length - prevLength + lost));
        prevLength = w.length;
        lost = 0;
        units.emplace_back(w.round, w.unitCount);
    }

    void spent(int n) { lost += n; }

    // Pearls a round we will eat over the rounds ahead (capFeedPays to the fold, roomHoldsSplit to the last round).
    // With P.intakeByLife, every pearl since our birth plus P.intakePriorTurns turns at the team's measured mean
    // P.capFeedTeamGrowth, over our turns plus those: a newborn reads the mean, a long record reads mostly itself.
    // Without it, our last P.intakeWindow turns, the missing turns of a younger dragon at that mean.
    double rate() const {
        if (P.intakeByLife)
            return (std::accumulate(eaten.begin(), eaten.end(), 0) + P.capFeedTeamGrowth * P.intakePriorTurns) /
                   (eaten.size() + P.intakePriorTurns);
        int const n = std::min<int>(P.intakeWindow, static_cast<int>(eaten.size()));
        int sum = 0;
        for (int i = static_cast<int>(eaten.size()) - n; i < static_cast<int>(eaten.size()); i++)
            sum += eaten[i];
        return (sum + P.capFeedTeamGrowth * (P.intakeWindow - n)) / P.intakeWindow;
    }

    // The team's net unit growth (splits less deaths) a round over our last P.intakeWindow turns, or 0 (read as not
    // growing) until we have seen that many: over fewer, one split or death elsewhere swamps it.
    double unitGrowth() const {
        if (units.size() <= static_cast<size_t>(P.intakeWindow))
            return 0;
        auto const& from = units[units.size() - P.intakeWindow - 1];
        auto const& to = units.back();
        return double(to.second - from.second) / (to.first - from.first);
    }

    // The team's count over our turns of the last `rounds` rounds, or of our whole life when shorter (fold.hpp's
    // UnitTrend), unknown until we have seen P.intakeWindow turns.
    UnitTrend unitTrend(int now, int rounds) const {
        UnitTrend t;
        if (units.size() <= static_cast<size_t>(P.intakeWindow))
            return t;
        size_t from = units.size() - 1;
        while (from > 0 && units[from - 1].first >= now - rounds)
            from--;
        t.rounds = now - units[from].first;
        t.known = t.rounds > 0;
        t.change = units.back().second - units[from].second;
        for (size_t i = from + 1; i < units.size(); i++)
            t.losses += std::max(0, units[i - 1].second - units[i].second);
        return t;
    }
};

// What a death costs the king below the ceiling where ordinary splits stop, in king length, when the team is `below`
// slots under it, growing `unitRate` units a round (splits less deaths, Intake::unitGrowth), `roundsToFold` rounds
// before the fold; 0 at or above the ceiling, where the next blocked split refills the slot at once. With
// P.unitCostByRefill the team is a unit short until its count regains its level, 1 / unitRate rounds (the whole way to
// the fold when it isn't growing), and a unit eats `growth` pearls a round (our own intake stands for it), a
// P.capFeedFoldShare of whose half the fold brings the king. Without it, E0273's fitted P.capFeedUnitValue a round the
// slot stays empty, until the team has grown by below + 1 units.
inline double unitCost(int below, double unitRate, double growth, int roundsToFold) {
    if (below <= 0)
        return 0;
    if (P.unitCostByRefill) {
        double const refill = unitRate > 0 ? 1 / unitRate : roundsToFold;
        return P.capFeedFoldShare * growth * std::min<double>(roundsToFold, refill) / 2;
    }
    double const empty = unitRate > 0 ? (below + 1) / unitRate : roundsToFold;
    return P.capFeedUnitValue * std::min<double>(roundsToFold, empty);
}

// Whether a feed now beats staying alive, in length the king gets: now it eats a share P.capFeedShare of our drop,
// ceil(L/2) pearls; alive we grow `growth` pearls a round until the fold starts (foldStart), and the fold brings a
// share P.capFeedFoldShare of that longer dragon's drop to the king; below the ceiling the unit our death frees costs
// `unitPrice` (unitCost) on the alive side too. P.capFeedMargin is what a feed must win by.
inline bool capFeedPays(int length, double growth, int roundsToFold, double unitPrice = 0) {
    double const now = P.capFeedShare * ((length + 1) / 2);
    double const later = P.capFeedFoldShare * std::ceil((length + growth * roundsToFold) / 2.0);
    return now >= later + unitPrice + P.capFeedMargin;
}

// capFeedDirection's answer: `dir` the direction to die by (kelp or our own body), DIE_IN_PLACE for an illegal split,
// or -1; else `approach`, the king's head tile to walk to (approachKing), or -1.
struct CapFeed {
    static constexpr int DIE_IN_PLACE = 4;
    int dir = -1;
    int approach = -1;
};

// A short dragon feeds our king (P.capFeed): at the unit cap a death costs no unit, since the next blocked split
// refills the slot, while the king eats our pearls now instead of only through the final fold. With P.capFeedBelowCap
// also below the cap while the king seed runs (fold.hpp's kingSeedOn: big maps from P.kingSeedRound or near the cap,
// smaller ones while the team's count has stalled),
// the unit it costs (unitCost) priced into the alive side.
// Fires while the team is at the ceiling where ordinary splits stop (unitLimit - rescueReserve), or under it as above,
// when the longest teammate we know of (in view, counting a fresh king report of it; with P.capFeedApproach a fresh
// report of one out of view, or longer than every teammate in view, counts too) is at least P.capFeedKingRatio times
// our length, capFeedPays at our intake rate with the unit's cost at the team's unit growth (Intake::unitGrowth), and:
// - no enemy head is in view, or with P.capFeedByRace our visible heads reach every tile of our drop strictly before
//   every visible enemy head (harvest.hpp's feedDropEdge, their paid sprints counted);
// - the king reaches our head within P.mergeRadius steps strictly before every other visible head;
// - the king has no pearl we see within P.mergeRadius of its head still to eat (so feeds come one at a time and the
//   king eats each drop, as far as our 7x7 view shows).
// It dies into kelp or our own body (as feedDirection), else with P.capFeedInPlace by an illegal split, which kills
// us where we stand. With P.capFeedApproach and no enemy head in view, when the distance fails (the king farther than
// P.mergeRadius, or known only from a report whose age is at most P.driftFactor x our path to its head, as fold.hpp's
// foldKing trusts one) or the king is near but another head or a pearl comes first (notfirst, busy), and the king's
// head is within the rounds left before the fold, `approach` is set: the dragon walks to it (approachKing) so a later
// turn can feed. Not for a folding dragon (feedDirection merges it). `field` is scratch, and on an approach holds the
// path fill from the king's head. With kNotes, notes.capFeed holds "fire", "walk" (an approach from afar), "notfirst"
// or "busy" (with or without an approach) or the gate that returned -1: under, nomate, longer (a fresh king report
// longer than every teammate in view), ratio, value, enemy, far or nodeath; notes.capGrowth our intake rate once it
// was read; notes.capBelow the slots under the ceiling.
inline CapFeed capFeedDirection(World const& w, Belief const& b, Intake const& intake, DistField& field) {
    auto const why = [](char const* gate) {
        if constexpr (kNotes)
            notes.capFeed = gate;
        (void)gate;
        return CapFeed{};
    };
    int const below = w.unitLimit - rescueReserve(w) - w.unitCount;
    if constexpr (kNotes)
        notes.capBelow = below;
    if (below > 0 && !(P.capFeedBelowCap && kingSeedOn(w)))
        return why("under");
    // A visible teammate's length: its visible chain, or its fresh king report when longer (7x7 vision often shows a
    // long king's front only).
    auto const* k = b.bestKing(w.round, P.kingReportAge, w.myId);
    auto const lengthOf = [&](SeenDragon const& d) {
        return k && k->rec.id == d.id ? std::max(d.visibleLen, k->rec.length) : d.visibleLen;
    };
    SeenDragon const* mate = nullptr;
    int mateLen = 0;
    bool enemy = false;
    for (auto const& d : w.dragons) {
        if (d.enemy)
            enemy = true;
        else if (int const len = lengthOf(d); d.id != w.myId && (!mate || len > mateLen || (len == mateLen && d.id < mate->id))) {
            mate = &d;
            mateLen = len;
        }
    }
    if (enemy && !P.capFeedByRace && !P.capFeedApproach)
        return why("enemy");
    // The king out of view (or a reported one longer than every teammate in view): only walked to.
    int farKing = -1;
    if (k && (!mate || (k->rec.id != mate->id && k->rec.length > mateLen))) {
        if (!P.capFeedApproach || k->rec.where < 0 || k->rec.where >= w.N)
            return why(mate ? "longer" : "nomate");
        farKing = k->rec.where;
        mateLen = k->rec.length;
    }
    if (!mate && farKing < 0)
        return why("nomate");
    if (mateLen < P.capFeedKingRatio * w.length)
        return why("ratio");

    double const growth = intake.rate();
    if constexpr (kNotes)
        notes.capGrowth = growth;
    int const roundsToFold = std::max(0, foldStart() - w.round);
    if (!capFeedPays(w.length, growth, roundsToFold, unitCost(below, intake.unitGrowth(), growth, roundsToFold)))
        return why("value");

    // Walks to the king whose head is at `head` (reported `age` rounds ago, 0 in view) when its path is within the
    // rounds left before the fold; `gate` is the note either way.
    auto const walk = [&](int head, int age, char const* gate) {
        if (!P.capFeedApproach || enemy)
            return why(enemy ? "enemy" : gate);
        field.build(w, head, roundsToFold + 1, w.head);
        int const path = field.at(w.head);
        if (path > roundsToFold || age > P.driftFactor * path)
            return why(gate);
        if constexpr (kNotes)
            notes.capFeed = gate == std::string_view("far") ? "walk" : gate;
        return CapFeed{-1, head};
    };
    if (farKing >= 0)
        return walk(farKing, w.round - k->rec.round, "far");
    if (enemy && !P.capFeedByRace)
        return why("enemy");

    field.build(w, w.head, P.mergeRadius + 1);
    int const mateDist = field.at(mate->head);
    if (mateDist > P.mergeRadius)
        return walk(mate->head, 0, "far");
    for (auto const& d : w.dragons)
        if (d.id != w.myId && d.id != mate->id && field.at(d.head) <= mateDist)
            return d.enemy ? why("enemy") : walk(mate->head, 0, "notfirst");
    for (int t : w.touched)
        if (w.pearlNow[t] && w.torusDist(t, mate->head) <= P.mergeRadius)
            return walk(mate->head, 0, "busy");
    if (enemy && feedDropEdge(w) < (std::min(w.length, static_cast<int>(w.body.size())) + 1) / 2)
        return why("enemy");

    for (int dir = 0; dir < 4; dir++) {
        int const to = w.step(w.head, dir);
        if (to == KELP || (to >= 0 && w.occId[to] == w.myId && !w.occHead[to])) {
            if constexpr (kNotes)
                notes.capFeed = "fire";
            return {dir};
        }
    }
    if (P.capFeedInPlace) {
        if constexpr (kNotes)
            notes.capFeed = "fire";
        return {CapFeed::DIE_IN_PLACE};
    }
    return why("nodeath");
}

// With P.capFeedApproach, a dragon capFeedDirection sends to the king (`cap.approach`) targets the king's head, as
// fold.hpp's foldTowardKing does, unless its pearl target (not a fight's enemy head) is eaten within P.kingPearlReach
// steps. `field` holds capFeedDirection's path fill from the king's head and is swapped into the target's. Call after
// Targets::update; returns true when it retargeted.
inline bool approachKing(World const& w, CapFeed const& cap, Targets& targets, DistField& field) {
    if (cap.approach < 0 || !P.capFeed || !P.capFeedApproach)
        return false;
    if (targets.pearl >= 0 && !Targets::enemyHeadAt(w, targets.pearl) &&
        std::max(targets.pearlField.at(w.head), targets.pearlReady) <= P.kingPearlReach)
        return false;
    targets.pearl = cap.approach;
    targets.pearlReady = 0;
    std::swap(targets.pearlField, field);
    return true;
}

} // namespace bot
