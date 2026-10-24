#include "support.hpp"
#include "scoring.hpp"
#include "split.hpp"

#include <string>

// Shedding a trapped tail (shed.hpp): a long dragon bridging two dead ends, as on Autarky and Dilemma, splits its
// rear off so that a later split-out child starts in open ground instead of looping between the two dead ends.

namespace {

unswbc::Controller controllerFor(bot::World const& w) {
    unswbc::Controller controller(w.myId, unswbc::Team::A, unswbc::Direction::EAST, {}, w.unitLimit);
    controller.length = w.length;
    controller.unit_count = w.unitCount;
    controller.head.position = {w.xOf(w.head), w.yOf(w.head)};
    return controller;
}

int splitChoice(bot::World const& w) {
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    auto const best = bot::bestMove(w, safety, targets);
    return bot::shouldSplit(w, controllerFor(w), safety, best);
}

// A 1-wide corridor at x = 8 from y = 2 to y = 13, walled on both sides and at both ends, with pearls in the
// north end. Our length-8 dragon heads north at (8, 4); its tail is at (8, 11), two tiles from the south end. The
// corridor's only door, east at (8, 7), is under our body, so both ends are dead ends while we lie in it; without the
// door (`sealed`) it is a room with no way out (roomsplit.hpp).
bot::World corridorBetweenDeadEnds(int size = 16, bool sealed = false) {
    auto w = test::world(size, size);
    for (int y = 2; y <= 13; ++y) {
        test::wall(w, w.tileOf(8, y), 1);
        test::wall(w, w.tileOf(8, y), 3);
    }
    if (!sealed)
        w.setEdge(w.edgeOf(w.tileOf(8, 7), 1), bot::EDGE_OPEN);
    test::wall(w, w.tileOf(8, 2), 0);
    test::wall(w, w.tileOf(8, 13), 2);
    std::vector<int> tiles;
    for (int y = 4; y <= 11; ++y)
        tiles.push_back(w.tileOf(8, y));
    test::body(w, tiles);
    test::pearl(w, w.tileOf(8, 3));
    test::pearl(w, w.tileOf(8, 2));
    return w;
}

void shedsWhenTailIsTrappedToo() {
    auto w = corridorBetweenDeadEnds();
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    auto const best = bot::bestMove(w, safety, targets);
    test::require(best.dir == 0 && best.closed, "the only move is north into the closed north end");
    char const* rule = "none";
    test::require(bot::shouldSplit(w, controllerFor(w), safety, best, &rule) == P.childLength,
                  "with the tail in the closed south end too, the rear is shed so our tail moves up the corridor");
    test::require(std::string(rule) == "shed", "the debug log line names the shed");
}

// A shed is a rescue split: on a big map it may take the slots ordinary splits leave free (rescueReserve, state.hpp).
void shedUsesRescueSlots() {
    auto w = corridorBetweenDeadEnds(64);
    w.unitCount = w.unitLimit - 1;
    test::require(bot::rescueReserve(w) > 0, "on a 64 x 64 board the last slot is reserved from ordinary splits");
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    auto const best = bot::bestMove(w, safety, targets);
    char const* rule = "none";
    test::require(bot::shouldSplit(w, controllerFor(w), safety, best, &rule) == P.childLength &&
                      std::string(rule) == "shed",
                  "the last free slot, reserved from ordinary splits, takes a shed");
}

void noShedWhenTailReachesOpenGround() {
    auto w = corridorBetweenDeadEnds();
    // The corridor opens east beside the tail: a split-out child would walk out there.
    w.setEdge(w.edgeOf(w.tileOf(8, 12), 1), bot::EDGE_OPEN);
    test::require(splitChoice(w) == 0, "a tail with a way out needs no shedding; the dragon farms and splits out");
}

void noShedWhenHeadIsOutOfTheCorridor() {
    auto w = corridorBetweenDeadEnds();
    // Leaving the corridor: a side exit beside our head leads to open ground, with the tail still inside (and no
    // pearls left in the north end to draw us in).
    w.setEdge(w.edgeOf(w.tileOf(8, 4), 1), bot::EDGE_OPEN);
    for (int y : {2, 3})
        w.pearlNow[w.tileOf(8, y)] = 0;
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    auto const best = bot::bestMove(w, safety, targets);
    test::require(!best.closed, "the east exit beside the head leads to open ground");
    test::require(bot::shedTrappedTail(w, controllerFor(w), safety, best) == 0,
                  "a dragon walking out of a dead end keeps its body: only one heading into a pocket sheds");
    // The ordinary split still asks its own gates: the child's only exit, south, reaches two tiles.
    test::require(bot::shouldSplit(w, controllerFor(w), safety, best) == (P.splitChildSpace <= 2 ? P.childLength : 0),
                  "an ordinary split with the tail in the south end happens only if the child space gate allows it");
}

void noShedWithoutAFreeTailExit() {
    auto w = corridorBetweenDeadEnds();
    // A teammate fills the south end, so a shed child would be born with nowhere to go.
    test::dragon(w, 2, false, w.tileOf(8, 12), 2);
    w.occId[w.tileOf(8, 13)] = 2;
    w.occK[w.tileOf(8, 13)] = 0;
    w.touched.push_back(w.tileOf(8, 13));
    test::require(splitChoice(w) == 0, "a child with no free tile would die at once");
}

void followerDoesNotTrapTheTail() {
    auto w = corridorBetweenDeadEnds();
    // The south end opens onto the board, and a teammate follows two tiles behind our tail: it will move on.
    test::wall(w, w.tileOf(8, 13), 2);
    w.setEdge(w.edgeOf(w.tileOf(8, 13), 2), bot::EDGE_OPEN);
    test::dragon(w, 2, false, w.tileOf(8, 13), 1);
    test::require(splitChoice(w) == 0, "only walls and our own body trap a tail; another dragon's head does not");
}

void noShedBesideAnEnemyHead() {
    auto w = corridorBetweenDeadEnds();
    w.setEdge(w.edgeOf(w.tileOf(8, 4), 1), bot::EDGE_OPEN);
    test::dragon(w, 9, true, w.tileOf(9, 4), 3);
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    auto const best = bot::bestMove(w, safety, targets);
    test::require(best.dir == 0 && best.closed, "north into the closed end is still the move");
    test::require(bot::shouldSplit(w, controllerFor(w), safety, best) == 0,
                  "standing still to shed beside an enemy head invites a ram, as for any split");
}

// Out of sight, the tail is placed by how we were born: a split-out child's tail sits in the pocket its parent
// was doomed in.
bot::World bornLongInCorridor() {
    auto w = corridorBetweenDeadEnds();
    // Only the front half of the body is in view and tracked; the dragon is really twelve long.
    std::vector<int> tiles;
    for (int y = 4; y <= 7; ++y)
        tiles.push_back(w.tileOf(8, y));
    for (int y = 8; y <= 11; ++y) {
        w.occId[w.tileOf(8, y)] = -1;
        w.occK[w.tileOf(8, y)] = -1;
    }
    w.dragons.clear();
    test::body(w, tiles);
    w.length = 12;
    w.bornLength = 12;
    w.firstRound = w.round - 1;
    return w;
}

void splitOutChildShedsOnItsSecondTurn() {
    auto w = bornLongInCorridor();
    test::require(splitChoice(w) == P.childLength,
                  "born long and heading into a closed pocket with the tail out of sight: shed once");
}

void unseenTailShedsOnlyOnTheSecondTurn() {
    auto w = bornLongInCorridor();
    w.firstRound = w.round;
    test::require(splitChoice(w) == 0, "on the first turn the doomed parent still blocks where the shed child would go");
    w.firstRound = w.round - 2;
    test::require(splitChoice(w) == 0, "after the second turn we can no longer say where the unseen tail is");
    w.firstRound = w.round - 1;
    w.bornLength = P.childLength;
    test::require(splitChoice(w) == 0, "an ordinary child was born short: its tail is not in a doomed pocket");
}

// A starting dragon has no parent beside its tail, so with P.shedStarterAnyTurn it sheds on its first turn, or the
// first later one, with the tail out of sight and the best move closed, once.
void starterShedsOnAnyTurn() {
    auto w = bornLongInCorridor();
    w.firstRound = 0;
    for (int round : {0, 5}) {
        w.round = round;
        test::require(splitChoice(w) == (P.shedStarterAnyTurn ? P.childLength : 0),
                      "a long starter heading into a closed pocket with its tail unseen sheds from its first turn on");
    }
    w.round = 1;
    test::require(splitChoice(w) == P.childLength, "its second turn sheds with either setting, as before");
    w.shedRound = 0;
    w.round = 5;
    test::require(splitChoice(w) == 0, "once it has shed, an unseen tail tells it nothing more: no second shed");
}

// The enemy-head check stands still for a head beside ours by plain distance even through kelp, unless
// P.shedEnemyByPath counts steps.
void enemyThroughKelpHoldsByPlainDistance() {
    auto w = corridorBetweenDeadEnds();
    test::dragon(w, 9, true, w.tileOf(9, 4), 3);
    test::require(w.torusDist(w.head, w.tileOf(9, 4)) == 1 && !w.pathWithin(w.head, w.tileOf(9, 4), 6),
                  "the head is beside ours, but the corridor wall puts it more than six steps away by path");
    test::require(splitChoice(w) == (P.shedEnemyByPath && P.splitEnemyHeadRadius < 7 ? P.childLength : 0),
                  "an enemy head beside us through kelp holds the shed unless the check counts path steps");
}

} // namespace

int main() {
    return test::suite({
        {"trapped tail is shed", shedsWhenTailIsTrappedToo},
        {"shed uses rescue slots", shedUsesRescueSlots},
        {"tail with a way out is kept", noShedWhenTailReachesOpenGround},
        {"dragon leaving a dead end keeps its tail", noShedWhenHeadIsOutOfTheCorridor},
        {"no shed without a free tail exit", noShedWithoutAFreeTailExit},
        {"a follower does not trap the tail", followerDoesNotTrapTheTail},
        {"no shed beside an enemy head", noShedBesideAnEnemyHead},
        {"split-out child sheds on its second turn", splitOutChildShedsOnItsSecondTurn},
        {"unseen tail sheds only on the second turn", unseenTailShedsOnlyOnTheSecondTurn},
        {"starter sheds on any turn", starterShedsOnAnyTurn},
        {"enemy through kelp holds by plain distance", enemyThroughKelpHoldsByPlainDistance},
    });
}
