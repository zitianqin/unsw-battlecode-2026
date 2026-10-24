#include "support.hpp"
#include "ram.hpp"
#include "scoring.hpp"
#include "split.hpp"

// Shedding the body under threat (split.hpp shedLength): a long dragon a shorter enemy head can sprint-ram keeps
// a length-2 front and splits the rest off, from threatShedEnd as threatShedOn allows; ordinary splits can leave
// slots free for it (rescueReserve, state.hpp).

namespace {

constexpr int N = 0, S = 2, W = 3;

// Our length-`length` dragon with its head at (8,8) on a `size` x `size` board, body trailing west in open
// ground, in a team of 20.
bot::World dragonOfLength(int length, int size = 16) {
    auto w = test::world(size, size);
    w.head = w.tileOf(8, 8);
    std::vector<int> tiles;
    for (int i = 0; i < length; ++i)
        tiles.push_back(w.tileOf(8 - i, 8));
    test::body(w, tiles);
    w.unitCount = 20;
    return w;
}

unswbc::Controller controllerFor(bot::World const& w) {
    unswbc::Controller controller(w.myId, unswbc::Team::A, unswbc::Direction::EAST, {}, w.unitLimit);
    controller.length = w.length;
    controller.unit_count = w.unitCount;
    controller.head.position = {w.xOf(w.head), w.yOf(w.head)};
    return controller;
}

int shedFor(bot::World const& w, bool king = false) {
    bot::Safety safety;
    safety.prepare(w);
    bot::DistField field;
    return bot::shedLength(w, controllerFor(w), safety, field, king);
}

int splitFor(bot::World const& w) {
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    auto const best = bot::bestMove(w, safety, targets);
    return bot::shouldSplit(w, controllerFor(w), safety, best);
}

// The shed's child: everything but a length-2 front.
int childOf(bot::World const& w) { return w.length - unswbc::Constants::MIN_SIZE; }

void shedsBesideShorterHead() {
    auto w = dragonOfLength(8);
    test::dragon(w, 2, true, w.tileOf(9, 8), 3);
    test::require(shedFor(w) == childOf(w), "a length-3 head beside our length-8 head: keep a length-2 front, shed 6");
    auto boundary = dragonOfLength(8);
    test::dragon(boundary, 2, true, boundary.tileOf(9, 8), 7);
    test::require(shedFor(boundary) == childOf(boundary), "a head one shorter than us still gains by ramming");
}

void equalHeadIsNoThreat() {
    auto w = dragonOfLength(8);
    test::dragon(w, 2, true, w.tileOf(9, 8), 8);
    test::require(shedFor(w) == 0, "an enemy as long as us loses as much as we do by ramming: play on");
    auto mate = dragonOfLength(8);
    test::dragon(mate, 2, false, mate.tileOf(9, 8), 3);
    test::require(shedFor(mate) == 0, "a short teammate head is no threat");
}

void onlyWithinTheEnemysReachPlusSlack() {
    int const reach5 = (P.fullEnemyReach ? 5 - 1 : bot::ramReach(5)) + P.shedThreatSlack;
    auto far = dragonOfLength(8);
    test::dragon(far, 2, true, far.tileOf(8, 8 - reach5 - 1), 5);
    test::require(shedFor(far) == 0, "one free step beyond a length-5 head's sprint reach plus the slack: play on");
    auto near = dragonOfLength(8);
    test::dragon(near, 2, true, near.tileOf(8, 8 - reach5), 5);
    test::require(shedFor(near) == childOf(near), "exactly a length-5 head's sprint reach plus the slack away: shed");
    auto shortReach = dragonOfLength(8);
    test::dragon(shortReach, 2, true, shortReach.tileOf(8, 8 - bot::ramReach(3) - P.shedThreatSlack - 1), 3);
    test::require(shedFor(shortReach) == 0, "a length-3 head sprints less far");
}

void blockedPathIsNoThreat() {
    auto w = dragonOfLength(8);
    test::dragon(w, 2, true, w.tileOf(8, 10), 5);
    test::require(shedFor(w) == childOf(w), "two free steps south of us: in reach");
    for (int x = 7; x <= 9; ++x) {
        w.occId[w.tileOf(x, 9)] = 3; // a teammate's body lies between us
        w.touched.push_back(w.tileOf(x, 9));
    }
    // Around the body the head needs 2 + 2 * 2 = 6 steps.
    int const around = 6;
    int const reach = (P.fullEnemyReach ? 5 - 1 : bot::ramReach(5)) + P.shedThreatSlack;
    test::require(reach < around, "the detour is beyond the threat's reach");
    test::require(shedFor(w) == 0, "around the teammate's body the head is out of reach");
}

void shortOrFullDragonsDoNotShedAndLateOnesPerSwitch() {
    // With P.shedOnValue a shed pays at every length that leaves a legal child (the ram likelier than the child's
    // failure is worth: shedPays), so the shortest shedder is 4 long; without it, P.shedMinLength.
    test::require(!P.shedOnValue || P.shedRamChance * P.ramWGain > P.shedChildFail, "the fitted ram chance beats the child's risk");
    int const shortestLen = P.shedOnValue ? 2 * unswbc::Constants::MIN_SIZE : P.shedMinLength;
    auto shortDragon = dragonOfLength(shortestLen - 1);
    test::dragon(shortDragon, 2, true, shortDragon.tileOf(9, 8), 2);
    test::require(shedFor(shortDragon) == 0, "a dragon one shorter than the shortest shedder doesn't shed");
    auto shortest = dragonOfLength(shortestLen);
    test::dragon(shortest, 2, true, shortest.tileOf(9, 8), 2);
    test::require(shedFor(shortest) == childOf(shortest), "the shortest shedder sheds a head 2 long");
    auto full = dragonOfLength(8);
    test::dragon(full, 2, true, full.tileOf(9, 8), 3);
    full.unitCount = full.unitLimit;
    test::require(shedFor(full) == 0, "at the unit cap no split is possible: normal play");
    auto late = dragonOfLength(8);
    test::dragon(late, 2, true, late.tileOf(9, 8), 3);
    late.round = bot::threatShedEnd() - 1;
    test::require(shedFor(late) == childOf(late), "the last round before the endgame still sheds");
    late.round = bot::threatShedEnd();
    test::require(shedFor(late) == (P.longShedsInFold ? childOf(late) : 0),
                  "in the endgame (king beacon, final fold, merges) a long non-king sheds only with P.longShedsInFold");
    late.round = unswbc::Constants::MAX_ROUNDS - 1;
    test::require(shedFor(late, true) == (P.kingShedsInFold || P.longShedsInFold ? childOf(late) : 0),
                  "our king sheds up to the last round: a shorter head's ram would cost it all its length");
}

void blindTailFollowsTheSwitch() {
    auto w = dragonOfLength(8);
    test::dragon(w, 2, true, w.tileOf(9, 8), 3);
    w.length = 10; // two segments beyond the tracked body
    test::require(shedFor(w) == (P.shedNeedsTail ? 0 : childOf(w)),
                  "with the tail out of sight the dragon sheds unchecked unless P.shedNeedsTail");
}

void childNeedsRoomToWalkAway() {
    auto w = dragonOfLength(8);
    test::dragon(w, 2, true, w.tileOf(9, 8), 3);
    int const tail = w.body.back();
    test::wall(w, tail, N);
    test::wall(w, tail, S);
    int const a = w.step(tail, W);
    int const b = w.step(a, N);
    int const c = w.step(b, N);
    test::wall(w, a, S);
    test::wall(w, a, W);
    for (int t : {b, c}) {
        test::wall(w, t, W);
        test::wall(w, t, 1);
    }
    test::wall(w, c, N);
    test::require(shedFor(w) == 0, "the seen tail's only exit is a 3-tile pocket: a child of 6 would be trapped");
}

void longerHeadIsNoReasonToShed() {
    auto w = dragonOfLength(8);
    test::dragon(w, 2, true, w.tileOf(10, 8), 10);
    test::dragon(w, 3, false, w.tileOf(2, 2), 3);
    bot::Safety safety;
    safety.prepare(w);
    test::require(!bot::findRam(w, safety).steps.empty(), "a longer head two free steps away is rammed");
    test::require(shedFor(w) == 0, "a longer head is no reason to shed");
}

// The reserve: ordinary splits stop at unitLimit - rescueReserve, where rescueReserve is the larger of the big-map
// rescue slots and P.shedReserve; a shed under threat may take the reserved slots.
void shedReserveOnSmallMaps() {
    auto w = dragonOfLength(P.splitAtLength);
    w.round = 100;
    test::require(bot::rescueReserve(w) == P.shedReserve,
                  "below P.reserveMinArea tiles only the shed reserve is kept, before the endgame");
    w.unitCount = w.unitLimit - P.shedReserve - 1;
    test::require(splitFor(w) == P.childLength, "a slot beyond the reserve: an ordinary split");
    w.unitCount = w.unitLimit - P.shedReserve;
    test::require(P.shedReserve == 0 || splitFor(w) == 0, "only the reserve left: no ordinary split");
    w.round = bot::threatShedEnd();
    test::require(bot::rescueReserve(w) == (P.longShedsInFold ? P.shedReserve : 0),
                  "from the endgame the shed reserve is kept only while every long dragon still sheds");
}

void shedReserveOnBigMaps() {
    auto w = dragonOfLength(8, 64);
    test::require(bot::rescueReserve(w) == std::max(P.rescueSlots, P.shedReserve),
                  "on a big map the reserve is the larger of the rescue slots and the shed reserve");
    test::dragon(w, 2, true, w.tileOf(9, 8), 3);
    w.unitCount = w.unitLimit - 1;
    test::require(shedFor(w) == childOf(w), "the last free slot, reserved from ordinary splits, takes a shed");
}

} // namespace

int main() {
    return test::suite({
        {"sheds beside a shorter head", shedsBesideShorterHead},
        {"equal head or teammate is no threat", equalHeadIsNoThreat},
        {"only within the enemy's reach plus slack", onlyWithinTheEnemysReachPlusSlack},
        {"blocked path is no threat", blockedPathIsNoThreat},
        {"short or full dragons do not shed; late ones per switch", shortOrFullDragonsDoNotShedAndLateOnesPerSwitch},
        {"a blind tail follows shedNeedsTail", blindTailFollowsTheSwitch},
        {"child needs room to walk away", childNeedsRoomToWalkAway},
        {"longer head is rammed, not shed from", longerHeadIsNoReasonToShed},
        {"shed reserve on small maps", shedReserveOnSmallMaps},
        {"shed reserve on big maps", shedReserveOnBigMaps},
    });
}
