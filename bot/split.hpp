#pragma once

#include "enemy_reach.hpp"
#include "farm.hpp"
#include "helper.hpp"
#include "hunt.hpp"
#include "notes.hpp"
#include "roomsplit.hpp"
#include "safety.hpp"
#include "scoring.hpp"
#include "shed.hpp"
#include "state.hpp"

#include <algorithm>
#include <string_view>

namespace bot {

// When every move is fatal (the end of a farm, or any other trap), the segments to split off so that the
// child, our tail reversed, survives and only a length-2 parent dies. 0 when no split is possible or the
// child's head has no free tile to step to. With the tail out of sight we split anyway: the pearls dropped
// are the same if the child dies too.
inline int splitOutLength(World const& w, unswbc::Controller const& ct, MoveEval const& best) {
    int const child = w.length - unswbc::Constants::MIN_SIZE;
    if (best.score > P.doomedScore || !ct.can_split(child))
        return 0;
    if (static_cast<int>(w.body.size()) < w.length)
        return child;
    int const childHead = w.body.back();
    for (int dir = 0; dir < 4; dir++) {
        int const to = w.step(childHead, dir);
        if (to >= 0 && w.occId[to] < 0)
            return child;
    }
    return 0;
}

// Whether a dragon may shed under threat this round (shedLength): any dragon before threatShedEnd; from it (the fold,
// king beacon and merge cascade) with P.longShedsInFold every dragon, since a shorter head's ram costs a folder all its
// length (half of it dropped where the rammer eats) while a shed costs the length-2 front and its child can still fold;
// with only P.kingShedsInFold, one that believes it is king (`king`, fold.hpp's isKing), whose shed child is the heir
// (fold.hpp's splitOutHeir: born longer than P.childLength; so is any shed child from beaconStart, which reports
// as king until it hears of a longer one, as a split-out child does).
inline bool threatShedOn(World const& w, bool king) {
    return w.round < threatShedEnd() || P.longShedsInFold || (P.kingShedsInFold && king);
}

// Whether this dragon is long enough to shed under threat (shedLength). With P.shedOnValue: when the ram's expected
// cost that the shed saves, P.shedRamChance times the ram model's P.ramWGain (rammodel.hpp) per segment past the
// length-2 front, beats the shed's, the child's failure chance P.shedChildFail per segment of the child; both grow with
// the child, so any dragon long enough to leave a legal child sheds when the ram is likelier than the child's failure
// is worth. Without it, at least P.shedMinLength.
inline bool shedPays(World const& w) {
    int const child = w.length - unswbc::Constants::MIN_SIZE;
    if (!P.shedOnValue)
        return w.length >= P.shedMinLength;
    return child >= unswbc::Constants::MIN_SIZE && P.shedRamChance * P.ramWGain * child > P.shedChildFail * child;
}

// The id of the first enemy head (in w.dragons order) shorter than us by visibleLen that shedLength judges able to
// reach our head within its reach plus P.shedThreatSlack, -1 none: shedLength's threat test, for the debug line
// (shedRefusal). It repeats shedLength's loop rather than serving it, so the log adds no code to real games' path;
// change both together (test_debugline checks they agree). `full` and `field` as for shedLength.
inline int shedThreat(World const& w, DistField& field, bool full = P.fullEnemyReach) {
    for (auto const& d : w.dragons) {
        if (!d.enemy || d.head < 0 || d.visibleLen >= w.length)
            continue;
        bool threatened;
        if (full) {
            int const reach = enemyReach(w, d, true, P.shedUnseenTailReach) + P.shedThreatSlack;
            threatened = fullEnemyReaches(w, d, w.head, reach, -1, 0, true);
        } else {
            int const reach = ramReach(d.visibleLen) + P.shedThreatSlack;
            field.build(w, d.head, reach, w.head, true);
            threatened = field.at(w.head) <= reach;
        }
        if (threatened)
            return d.id;
    }
    return -1;
}

// Whether a shed child of length `child` (our tracked tail reversed) has an exit whose static-body physical fill
// reaches min(child + P.spaceMargin, holdCap) tiles: shedLength's check with the tail in view, repeated for
// shedRefusal as shedThreat is.
inline bool shedChildExits(World const& w, Safety& safety, int child) {
    int const need = std::min(child + P.spaceMargin, holdCap(w));
    int const childHead = w.body.back();
    safety.rivalField.build(w, w.head, need);
    for (int dir = 0; dir < 4; dir++) {
        int const to = w.step(childHead, dir);
        if (to < 0 || w.occId[to] >= 0)
            continue;
        FillOptions const childView{.staticBodies = true, .rival = &safety.rivalField, .physical = true};
        if (safety.floodFill(to, 1, need, childView) >= need)
            return true;
    }
    return false;
}

// Shedding the body under threat (P.shedUnderThreat): a shorter enemy head gains by ramming our long dragon, so
// while threatShedOn (`king` is isKing's answer) a dragon long enough (shedPays) that sees an enemy head shorter by visibleLen and able to reach our head within its own
// sprint reach plus P.shedThreatSlack steps over visible empty tiles splits everything but a
// length-2 front off. P.fullEnemyReach counts every step the enemy's length and the pearls on its path pay for
// (enemy_reach.hpp), with enemyReach's unseen-tail fallback under P.shedUnseenTailReach; off uses the legacy
// ramReach cap. The ram then kills only the front, and a bot that rams only heads at least as
// long as itself no longer wants it; the child (our rear, reversed) walks away from the old tail. With our tail in
// view the child needs an exit whose static-body physical fill reaches min(child + P.spaceMargin, holdCap) tiles;
// with it out of sight the dragon sheds unchecked, as splitOutLength does, unless P.shedNeedsTail. It uses any
// free slot, the reserve included (rescueReserve, state.hpp). Returns the child length, 0 for none. `field` is
// scratch space. main() asks after findRam and findEscape (ramming a longer head and sprinting out of reach go
// first) and before feeding, hunting and splitting.
inline int shedLength(World const& w, unswbc::Controller const& ct, Safety& safety, DistField& field,
                      bool king = false, bool full = P.fullEnemyReach) {
    int const child = w.length - unswbc::Constants::MIN_SIZE;
    if (!P.shedUnderThreat || !threatShedOn(w, king) || !shedPays(w) || !ct.can_split(child))
        return 0;
    bool const tailSeen = static_cast<int>(w.body.size()) >= w.length;
    if (P.shedNeedsTail && !tailSeen)
        return 0;
    bool threatened = false;
    for (auto const& d : w.dragons) {
        if (threatened || !d.enemy || d.head < 0 || d.visibleLen >= w.length)
            continue;
        if (full) {
            int const reach = enemyReach(w, d, true, P.shedUnseenTailReach) + P.shedThreatSlack;
            threatened = fullEnemyReaches(w, d, w.head, reach, -1, 0, true);
        } else {
            int const reach = ramReach(d.visibleLen) + P.shedThreatSlack;
            field.build(w, d.head, reach, w.head, true);
            threatened = field.at(w.head) <= reach;
        }
    }
    if (!threatened)
        return 0;
    if (!tailSeen)
        return child;
    int const need = std::min(child + P.spaceMargin, holdCap(w));
    int const childHead = w.body.back();
    safety.rivalField.build(w, w.head, need);
    for (int dir = 0; dir < 4; dir++) {
        int const to = w.step(childHead, dir);
        if (to < 0 || w.occId[to] >= 0)
            continue;
        FillOptions const childView{.staticBodies = true, .rival = &safety.rivalField, .physical = true};
        if (safety.floodFill(to, 1, need, childView) >= need)
            return child;
    }
    return 0;
}

// Why a threatened dragon long enough to shed (shedPays) didn't shed this turn, for the debug line (main, with kNotes):
// `threat` is shedThreat's id (-1 none: then no reason); `rule` the rule that took the turn. The first of: ram or
// escape (they play before the shed), off (P.shedUnderThreat), round (threatShedOn is false), slot (ct.can_split
// refuses the child), tail (P.shedNeedsTail with the tail out of sight), exit (the tail in view and no child exit
// reaches the fill), else none. Null when the turn shed ("threatshed") or no head threatens us.
inline char const* shedRefusal(World const& w, unswbc::Controller const& ct, Safety& safety, int threat, bool king,
                               char const* rule) {
    if (threat < 0 || std::string_view(rule) == "threatshed")
        return nullptr;
    int const child = w.length - unswbc::Constants::MIN_SIZE;
    bool const tailSeen = static_cast<int>(w.body.size()) >= w.length;
    return std::string_view(rule) == "ram" || std::string_view(rule) == "escape" ? rule
           : !P.shedUnderThreat                                                    ? "off"
           : !threatShedOn(w, king)                                                ? "round"
           : !ct.can_split(child)                                                  ? "slot"
           : P.shedNeedsTail && !tailSeen                                          ? "tail"
           : tailSeen && !shedChildExits(w, safety, child)                         ? "exit"
                                                                                   : "none";
}

// The parent's share of the ground past its best move, capped at `cap` (P.splitParentTerritory). With
// P.splitTerritoryEnemyOnly, only enemy heads contest it, so teammates crowding open ground don't hold back a
// split; otherwise it is evaluateMove's space, contested by every other head.
inline int splitTerritory(World const& w, Safety& safety, MoveEval const& best, int cap) {
    if (!P.splitTerritoryEnemyOnly)
        return best.space;
    safety.enemyField.buildEnemyHeads(w, cap + 1);
    FillOptions const enemyView{.rival = &safety.enemyField, .physical = true};
    return safety.floodFill(best.dest, w.hasPearlHere(best.dest) ? 1 : 0, cap, enemyView);
}

// The split-at-4 swarm: split a P.childLength child off the tail whenever we are at least P.splitAtLength
// long, before P.mergeRound, under the unit cap less the slots kept free for split-outs and sheds (rescueReserve,
// state.hpp), with no enemy head next to us, as long as our best move
// (`best`, from bestMove) is open and fits our body with it standing still (not checked at exactly P.splitAtLength
// without P.splitSpaceGates), and the child's head (our tail) has a free tile (a dragon at exactly P.splitAtLength
// with its tail out of sight splits blind with P.splitUntrackedTail, past every later check). Three room gates can hold splits
// back further, each off at 0 (their default, as the out-breeding leaders play): the parent's move must physically
// reach P.splitParentSpace tiles, of which P.splitParentTerritory before every enemy head (every other head without P.splitTerritoryEnemyOnly), and
// the child needs an exit whose fill reaches P.splitChildSpace tiles, and with P.splitExitMustBeSeen one this dragon
// has had in view (a split child's tail touches its parent's body, which a newborn can't see). With
// P.closedRoomSplits, a child that would start in a room it knows no way out of splits only when the room holds both halves for the rounds left
// (roomsplit.hpp's roomHoldsSplit, at `intakeRate` pearls a round each: main passes our Intake::rate).
// Returns the child length to split off, 0 for none; a doomed dragon splits out (splitOutLength) first, and one
// heading into a closed pocket with its tail trapped in another sheds its rear (shed.hpp, which asks roomHoldsSplit
// too) at any round; with
// `holdOrdinary` (a seeding king, fold.hpp's kingSeedHolds) those are all it does. When it
// splits, `rule` (if given) names which: "splitout", "shed" or "split", for main's debug log line. With kNotes, a
// dragon that doesn't split leaves the first gate that held it back in notes.splitHeld: merge (P.mergeRound), short,
// cap (the unit limit) or size (can_split refuses the child), reserve (rescueReserve's slots), tail (body untracked),
// room (best closed or short of room; room and fill hold a dragon at exactly P.splitAtLength only with
// P.splitSpaceGates, tail only with it or without P.splitUntrackedTail), enemy (P.splitEnemyHeadRadius), territory, fill (the standing-body parent fill)
// child (no exit for the child), unseen (the child's only free exits were never in view: P.splitExitMustBeSeen),
// closedroom (roomHoldsSplit), or seed (every gate passed but holdOrdinary held the split back).
inline int shouldSplit(World const& w, unswbc::Controller const& ct, Safety& safety, MoveEval const& best,
                       char const** rule = nullptr, bool holdOrdinary = false,
                       double intakeRate = P.capFeedTeamGrowth) {
    auto const named = [rule](int child, char const* name) {
        if (rule && child)
            *rule = name;
        return child;
    };
    if (int const out = splitOutLength(w, ct, best))
        return named(out, "splitout");
    if (int const shed = shedTrappedTail(w, ct, safety, best, intakeRate))
        return named(shed, "shed");
    int const child = P.childLength;
    // Every gate passed: the ordinary split, unless holdOrdinary keeps it back.
    auto const ordinary = [&] {
        if (!holdOrdinary)
            return named(child, "split");
        if constexpr (kNotes)
            notes.splitHeld = "seed";
        return 0;
    };
    if (w.round >= P.mergeRound || w.length < P.splitAtLength || !ct.can_split(child)) {
        if constexpr (kNotes)
            notes.splitHeld = w.round >= P.mergeRound       ? "merge"
                              : w.length < P.splitAtLength  ? "short"
                              : w.unitCount >= w.unitLimit ? "cap"
                                                            : "size";
        return 0;
    }
    if (w.unitCount >= w.unitLimit - rescueReserve(w)) {
        if constexpr (kNotes)
            notes.splitHeld = "reserve";
        return 0;
    }
    // Without P.splitSpaceGates a dragon at exactly P.splitAtLength splits past the tail and parent space gates, as
    // Cache me outside does; a longer one keeps them, since its mass is what the king line grows from.
    bool const gated = P.splitSpaceGates || w.length > P.splitAtLength;
    if (static_cast<int>(w.body.size()) < w.length) {
        if (!gated && P.splitUntrackedTail)
            return ordinary(); // split blind, as Cache me outside's starters do: no child exit check is possible
        if constexpr (kNotes)
            notes.splitHeld = "tail";
        return 0; // tail never seen: we cannot check the child's exits
    }
    int const parentNeed = std::max(w.length - child + P.spaceMargin, P.splitParentSpace);
    if (gated && (best.closed || best.room < parentNeed)) {
        if constexpr (kNotes)
            notes.splitHeld = "room";
        return 0;
    }
    for (auto const& d : w.dragons) {
        if (d.enemy && w.torusDist(d.head, w.head) <= P.splitEnemyHeadRadius) {
            if constexpr (kNotes)
                notes.splitHeld = "enemy";
            return 0;
        }
    }
    if (P.splitParentTerritory > 0 && splitTerritory(w, safety, best, P.splitParentTerritory) < P.splitParentTerritory) {
        if constexpr (kNotes)
            notes.splitHeld = "territory";
        return 0;
    }

    // A split leaves the parent standing still and hands our tail to the child, so neither half may count
    // on body tiles freeing up, and each loses ties to the other's head.
    int const childHead = w.body.back();
    safety.rivalField.build(w, childHead, parentNeed);
    FillOptions const parentView{.staticBodies = true, .rival = &safety.rivalField, .physical = true};
    if (gated && safety.floodFill(best.dest, 0, parentNeed, parentView) < parentNeed) {
        if constexpr (kNotes)
            notes.splitHeld = "fill";
        return 0;
    }

    if (P.splitChildSpace > 1)
        safety.rivalField.build(w, w.head, P.splitChildSpace);
    bool childExit = false;
    bool unseenExit = false, seenExit = false;
    for (int dir = 0; dir < 4 && !childExit; dir++) {
        int const to = w.step(childHead, dir);
        if (to < 0 || w.occId[to] >= 0)
            continue;
        if (P.splitExitMustBeSeen && w.lastSeen[to] < 0) {
            unseenExit = true;
            continue;
        }
        seenExit = true;
        FillOptions const childView{.staticBodies = true, .rival = &safety.rivalField, .physical = true};
        childExit = P.splitChildSpace <= 1 || safety.floodFill(to, 1, P.splitChildSpace, childView) >= P.splitChildSpace;
    }
    if (!childExit) {
        if constexpr (kNotes)
            notes.splitHeld = unseenExit && !seenExit ? "unseen" : "child";
        return 0;
    }
    if (!roomHoldsSplit(w, safety, childHead, child, intakeRate)) {
        if constexpr (kNotes)
            notes.splitHeld = notes.roomHeld = "closedroom";
        return 0;
    }
    return ordinary();
}

} // namespace bot
