#pragma once

#include "belief.hpp"
#include "feed.hpp"
#include "helper.hpp"
#include "notes.hpp"
#include "state.hpp"
#include "targets.hpp"

#include <algorithm>
#include <cmath>
#include <utility>

namespace bot {

// The final fold: the round-500 tiebreak is our longest dragon, and the merge cascade (feed.hpp) only folds
// dragons that meet, so dragons far apart end the game separate. The king tells the team where it is, and
// late in the game dragons with time to get there walk to it and die beside it so it eats their pearls.
// P.localFold keeps distant dragons breeding on big maps; foldPathCap sets the travel limit.

// Seeding the king (P.kingSeedOnValue): the round-500 tiebreak is our longest dragon, a king eats at
// most one pearl a move, so its length when the final fold starts decides; a child split off it returns about one
// length to it by the end whenever it is born, less than the P.childLength it takes. The king report and the hold
// run on a map of at least P.kingSeedArea tiles, before P.mergeRound, while a child is worth less to the king than
// that: with the team within P.kingHoldLead slots of where ordinary splits stop (unitLimit - rescueReserve), where
// another dragon's split refills a freed slot within a round or two so the king's child adds no unit, and from
// P.kingSeedRound, when too few rounds are left for a child to be worth a unit to the team. On a smaller map, with
// P.kingSeedStall, they run while the team's count has stalled (unitStall). The report reaches few of our dragons,
// so several that each think themselves king hold at once.
//
// The team's unit count over the past, from this process's own turns (capfeed.hpp's Intake::unitTrend): known once
// they span P.intakeWindow turns (over fewer, one split or death elsewhere swamps it), the rounds it spans (our life,
// up to the rounds left: a trend is read over as long a past as the future it is projected over, when we have one),
// its net change over them (splits less deaths) and the units it lost between our turns (every fall of the count, a
// lower bound on its deaths). Set once a turn by main (teamTrend).
struct UnitTrend {
    bool known = false;
    int rounds = 0;
    int change = 0;
    int losses = 0;
};
inline UnitTrend teamTrend;

// With P.kingSeedStall: the team's count has stopped growing while it loses units, so a child the king splits off adds
// no lasting unit (births only replace deaths): once the game has run at least as long as it has left (so not before
// MAX_ROUNDS / 2; earlier, trends extrapolated from a few turns seeded kings from r23 and lost games, E0505), at
// its growth over teamTrend's span (a young dragon's is shorter than the rounds left) the team adds fewer than one
// more unit in the rounds left; it lost at least one unit (a count flat because nobody split is no stall: there the king's
// hold would be what keeps it flat); and the team outlasts the game at that rate: unitCount - 1 exceeds its net loss a
// round times the rounds left (a swarm wiping us out keeps us breeding).
inline bool unitStall(World const& w) {
    int const left = unswbc::Constants::MAX_ROUNDS - w.round;
    if (!P.kingSeedStall || !teamTrend.known || teamTrend.losses <= 0 || left > w.round)
        return false;
    double const projected = double(teamTrend.change) * left / teamTrend.rounds;
    return projected < 1 && w.unitCount - 1 > -projected;
}

inline bool kingSeedOn(World const& w) {
    if (!P.kingSeedOnValue || w.round >= P.mergeRound)
        return false;
    if (w.N < P.kingSeedArea)
        return unitStall(w);
    return w.round >= P.kingSeedRound || w.unitCount >= w.unitLimit - rescueReserve(w) - P.kingHoldLead;
}

// True when we are the king as far as we know: with P.kingBeacon, from beaconStart (or while kingSeedOn), at least
// P.kingMinLength long, and there is no visible teammate or fresh king report we would merge into.
inline bool isKing(World const& w, Belief const& b) {
    if (!P.kingBeacon || (w.round < beaconStart() && !kingSeedOn(w)) || w.length < P.kingMinLength)
        return false;
    for (auto const& d : w.dragons)
        if (!d.enemy && d.id != w.myId && mergesInto(w.length, w.myId, d.visibleLen, d.id))
            return false;
    auto const* k = b.bestKing(w.round, P.kingReportAge, w.myId);
    return !(k && mergesInto(w.length, w.myId, k->rec.length, k->rec.id));
}

// With P.modeModel (I485), the rounds before MAX_ROUNDS at which the final fold starts: P.foldWindow plus
// P.foldWindowPerKTileSq times the square of the map's size in 1000s of tiles, within the windows the fit played
// (P.foldWindowMin to P.foldWindowMax). Fitted (E0345) on the game results of copies that differed only in their fold
// start, on the same maps and sides: the bigger the map, the earlier the fold pays (on maps of P.localFoldArea tiles
// or more only dragons near the king fold, so an early fold there costs few units); on small maps it is about the
// fixed start.
inline double foldWindowFor(World const& w) {
    double const k = w.N / 1000.0;
    return std::clamp(P.foldWindow + P.foldWindowPerKTileSq * k * k, P.foldWindowMin, P.foldWindowMax);
}

// Call once a turn before reportKing: with P.modeModel, sets the fold's start (state.hpp's modelFoldStart) from the
// fold window (foldWindowFor).
inline void startFold(World const& w) {
    if (P.modeModel)
        modelFoldStart = unswbc::Constants::MAX_ROUNDS - static_cast<int>(std::lround(foldWindowFor(w)));
}

// With P.kingHoldsSplits, from foldStart the king makes no ordinary splits: a child would only fold back into
// it and lose half its length again.
inline bool kingHoldsSplits(World const& w, Belief const& b) {
    return P.kingHoldsSplits && P.finalFold && w.round >= foldStart() && isKing(w, b);
}

// While kingSeedOn, the king makes no ordinary splits (shouldSplit's holdOrdinary: split-outs and trapped-tail sheds
// still run) and no farm trips, each of which ends in a split-out (farm.hpp).
inline bool kingSeedHolds(World const& w, Belief const& b) { return kingSeedOn(w) && isKing(w, b); }

// Whether the king refuses closed-pocket farm entries this turn (evaluateMoves' mayFarm): while it seeds
// (kingSeedHolds), and with P.kingFoldSkipsFarms, on any map while it holds its splits in the fold (kingHoldsSplits).
inline bool kingSkipsFarms(World const& w, Belief const& b) {
    return kingSeedHolds(w, b) || (P.kingFoldSkipsFarms && kingHoldsSplits(w, b));
}

// With P.kingSeeksFeedersInFold, from foldStart a dragon that believes it is king (isKing) asks seekFeeder to walk it to
// its feeders, not only from P.mergeRound: folders merge into it from the fold's start (feedDirection). Several
// dragons believe it at once, since king reports reach few of them; with P.foldSeekerHeardLongest only one at least as
// long as every teammate it has heard of this game (Belief::longestHeard) seeks.
inline bool kingSeeksInFold(World const& w, Belief const& b) {
    return P.kingSeeksFeedersInFold && P.finalFold && w.round >= foldStart() && isKing(w, b) &&
           (!P.foldSeekerHeardLongest || w.length >= b.longestHeard(w.round, w.myId));
}

// With P.kingSuccession, a king that splits out (split.hpp's splitOutLength) passes the crown to its child at once,
// instead of when the dying length-2 parent's report ages out. The child is a new id, MIN_SIZE shorter than the
// parent, and its head was the parent's tail. A split-out child (born longer than P.childLength, from
// beaconStart, or on a map of at least P.kingSeedArea, where kingSeedOn seeds a king before the beacon: on a smaller
// one a child reads no unit trend, so seeds nothing, until it has P.intakeWindow turns behind it) that is king marks its reports as the heir's for P.kingReportAge rounds, and Belief::superseded
// drops every report filed by its birth of a king it may have come from. The child itself often hears its parent's last reports
// (they would stop it being king), so findParentKing finds them: within P.kingReportAge rounds of our birth, a
// fresh report filed no later than it, of a lower id, bornLength to bornLength + MIN_SIZE long, whose head could
// reach our birth head (within that length less one, plus a tile a round the parent moved since), is our parent,
// and Belief::bestKing ignores its reports up to our birth (a later one is a live king's, not our parent's).
inline bool splitOutHeir(World const& w) {
    return P.kingSuccession && (w.firstRound >= beaconStart() || (P.kingSeedOnValue && w.N >= P.kingSeedArea)) &&
           w.bornLength > P.childLength &&
           w.round - w.firstRound <= P.kingReportAge;
}

inline void findParentKing(World const& w, Belief& b) {
    if (!splitOutHeir(w) || b.heirOf >= 0 || w.bornHead < 0)
        return;
    int const maxLen = w.bornLength + unswbc::Constants::MIN_SIZE;
    Record const* parent = nullptr;
    for (auto const& k : b.kings) {
        Record const& r = k.rec;
        if (r.id >= w.myId || r.round > w.firstRound || w.round - r.round > P.kingReportAge ||
            r.length < w.bornLength || r.length > maxLen ||
            w.torusDist(r.where, w.bornHead) > maxLen - 1 + (w.firstRound - r.round))
            continue;
        if (!parent || r.length > parent->length || (r.length == parent->length && r.id < parent->id))
            parent = &r;
    }
    if (parent) {
        b.heirOf = parent->id;
        b.heirOfRound = w.firstRound;
    }
}

// Call after Belief::hear: a king files a report of itself (head, length, round), which gossip then sends
// ahead of everything else and teammates relay. A split-out child marks its reports as the heir's until its
// parent's last report has aged out (splitOutHeir), so teammates drop the parent's; before beaconStart (a seeding
// king, kingSeedOn) only once it has found that parent's reports (findParentKing), since the parent may have been no king.
inline void reportKing(World const& w, Belief& b) {
    findParentKing(w, b);
    if (!isKing(w, b))
        return;
    Record r;
    r.king = true;
    r.round = w.round;
    r.where = w.head;
    r.length = w.length;
    r.id = w.myId;
    r.teamB = w.teamB;
    r.heir = splitOutHeir(w) && (w.firstRound >= beaconStart() || b.heirOf >= 0);
    b.mergeKing(r);
}

// The rounds a folding dragon leaves the king to eat its drop. A dead dragon's ceil(L/2) pearls lie on every second
// segment, so eating them all takes about its length; with P.partialFold at most P.foldMinFeedRounds, so a long
// dragon too far to be eaten whole still folds while the king can eat part of it (rather than staying separate to
// round 500, where only our longest counts).
inline int foldFeedRounds(World const& w) {
    return P.partialFold ? std::min(w.length, P.foldMinFeedRounds) : w.length;
}

// The longest path to the king's head that leaves time to be eaten: the rounds left after this one less
// foldFeedRounds.
inline int foldTimeCap(World const& w) { return unswbc::Constants::MAX_ROUNDS - 1 - w.round - foldFeedRounds(w); }

// The longest path to the king's head a folding dragon may have: foldTimeCap, and with P.localFold on a map of at
// least P.localFoldArea tiles at most P.foldReach.
inline int foldPathCap(World const& w) {
    int const budget = foldTimeCap(w);
    return P.localFold && w.N >= P.localFoldArea ? std::min(budget, P.foldReach) : budget;
}

// The king report a folder walks to: the best (Belief::bestKing's order) of those at most P.kingReportAge rounds old
// and, with P.reportByDrift, of those whose age is at most P.driftFactor x our path to their reported head, that path
// within `budget` (foldPathCap). A king's head moves at most a tile a round, so while a report's age is no more than
// the path the king's drift is no more than the path, and the report still points a far folder the right way; one as
// old a few steps off may not. Walking to a stale report closes its window at twice the walk's pace (the age grows as
// the path shrinks). An older report's path is filled from its head in `field` (scratch), best report first, only
// until one is taken. isKing, the relays and the heir window keep P.kingReportAge.
inline Belief::KingReport const* foldKing(World const& w, Belief const& b, int budget, DistField& field) {
    int const window = P.reportByDrift && budget >= 0 ? static_cast<int>(P.driftFactor * budget) : 0;
    if (window <= P.kingReportAge)
        return b.bestKing(w.round, P.kingReportAge, w.myId);
    return b.bestKingIf(w.round, window, w.myId, [&](Belief::KingReport const& k) {
        int const age = w.round - k.rec.round;
        if (age <= P.kingReportAge)
            return true;
        if (k.rec.where >= w.N)
            return false;
        field.build(w, k.rec.where, budget + 1, w.head);
        int const path = field.at(w.head);
        return path <= budget && age <= P.driftFactor * path;
    });
}

// With P.finalFold, from foldStart(), while the team is big enough to merge (P.mergeMinUnits, unless
// foldPastFloor): a dragon at least P.foldMinLength long (by default any) that holds a king report (foldKing) of a
// king it merges into (mergesInto, at the reported length) folds if its path fits foldPathCap (time to reach the king
// and be eaten, whole or with P.partialFold in part). Unless its pearl target (not a fight's enemy head,
// Targets::addFights) is eaten within P.kingPearlReach steps, it targets the king's head (where we see it, else where it was reported). The returned Fold tells feedDirection
// to merge it into the king and main to stop
// its ordinary splits. Call after Targets::update (main calls seekFeeder only when this returns inactive); `field`
// is scratch space. With kNotes, an inactive result leaves its first gate in notes.foldGate: off (no P.finalFold),
// early (before foldStart), short (P.foldMinLength), floor (P.mergeMinUnits), noking (no king report foldKing takes),
// longer (we don't merge into the reported king), budget (no time to reach it) or reach (farther than P.foldReach).
inline Fold foldTowardKing(World const& w, Belief const& b, Targets& targets, DistField& field) {
    if (!P.finalFold || w.round < foldStart() || w.length < P.foldMinLength ||
        (w.unitCount <= P.mergeMinUnits && !foldPastFloor(w))) {
        if constexpr (kNotes)
            notes.foldGate = !P.finalFold ? "off" : w.round < foldStart() ? "early" : w.length < P.foldMinLength ? "short" : "floor";
        return {};
    }
    int const budget = foldPathCap(w);
    auto const* k = foldKing(w, b, budget, field);
    if (!k || !mergesInto(w.length, w.myId, k->rec.length, k->rec.id) || k->rec.where >= w.N) {
        if constexpr (kNotes)
            notes.foldGate = k && !mergesInto(w.length, w.myId, k->rec.length, k->rec.id) ? "longer" : "noking";
        return {};
    }
    int kingHead = k->rec.where;
    int kingLen = k->rec.length;
    for (auto const& d : w.dragons) {
        if (!d.enemy && d.id == k->rec.id && d.head >= 0) {
            kingHead = d.head;
            kingLen = std::max(kingLen, d.visibleLen);
        }
    }
    if (budget < 0) {
        if constexpr (kNotes)
            notes.foldGate = "budget";
        return {};
    }
    field.build(w, kingHead, budget + 1, w.head);
    if (field.at(w.head) > budget) {
        if constexpr (kNotes)
            notes.foldGate = budget < foldTimeCap(w) ? "reach" : "budget";
        return {};
    }
    if (targets.pearl < 0 || Targets::enemyHeadAt(w, targets.pearl) ||
        std::max(targets.pearlField.at(w.head), targets.pearlReady) > P.kingPearlReach) {
        targets.pearl = kingHead;
        targets.pearlReady = 0;
        std::swap(targets.pearlField, field);
    }
    return {true, k->rec.id, kingLen};
}

} // namespace bot
