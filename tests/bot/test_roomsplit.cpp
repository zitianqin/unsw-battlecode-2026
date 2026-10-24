// Notes on in this build only, so each case can say which gate held the split.
#define BOT_NOTES true

#include "support.hpp"
#include "scoring.hpp"
#include "split.hpp"

#include <string>

// Breeding in closed rooms (roomsplit.hpp): an ordinary split or a trapped-tail shed whose child would start in a
// room it knows no way out of goes ahead only when the room holds both halves for the rounds left.

namespace {

unswbc::Controller controllerFor(bot::World const& w) {
    unswbc::Controller controller(w.myId, unswbc::Team::A, unswbc::Direction::EAST, {}, w.unitLimit);
    controller.length = w.length;
    controller.unit_count = w.unitCount;
    controller.head.position = {w.xOf(w.head), w.yOf(w.head)};
    return controller;
}

struct Choice {
    int child = 0;
    std::string rule = "none";
    std::string held = "none"; // notes.roomHeld: which split the closed-room gate refused
};

Choice splitChoice(bot::World const& w, double rate = P.capFeedTeamGrowth) {
    bot::notes.clear();
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    auto const best = bot::bestMove(w, safety, targets);
    char const* rule = "none";
    Choice choice;
    choice.child = bot::shouldSplit(w, controllerFor(w), safety, best, &rule, false, rate);
    choice.rule = rule;
    if (bot::notes.roomHeld)
        choice.held = bot::notes.roomHeld;
    return choice;
}

// Our free tiles needed in a closed room holding our head (roomHoldsSplit): our length, and per half its margin and
// what it eats by the last round at `rate`.
double need(bot::World const& w, double rate) {
    return w.length + 2 * (P.spaceMargin + rate * (unswbc::Constants::MAX_ROUNDS - 1 - w.round));
}

// A 4x4 room (x 2..5, y 2..5) walled with kelp but for a portal on the east side of (5, 2), paired with the west
// side of (px, py), which the parent knows. Our length-4 dragon lies along y = 4, head west at (2, 4), tail at
// (5, 4), where a split child's head starts.
bot::World portalRoom(int px, int py) {
    auto w = test::world();
    auto const inside = [](int x, int y) { return x >= 2 && x <= 5 && y >= 2 && y <= 5; };
    for (int y = 2; y <= 5; y++)
        for (int x = 2; x <= 5; x++)
            for (int dir = 0; dir < 4; dir++)
                if (!inside(x + bot::DX[dir], y + bot::DY[dir]))
                    test::wall(w, w.tileOf(x, y), dir);
    w.rememberEdge(w.edgeOf(w.tileOf(5, 2), 1), unswbc::Edge(false, unswbc::EdgeType::PORTAL, 7));
    w.rememberEdge(w.edgeOf(w.tileOf(px, py), 3), unswbc::Edge(false, unswbc::EdgeType::PORTAL, 7));
    test::body(w, {w.tileOf(2, 4), w.tileOf(3, 4), w.tileOf(4, 4), w.tileOf(5, 4)});
    return w;
}

void childWithoutAWayOutIsNotBred() {
    auto w = portalRoom(12, 12);
    test::require(w.step(w.tileOf(5, 2), 1) == w.tileOf(12, 12), "the parent knows where the portal leads");
    test::require(need(w, P.capFeedTeamGrowth) > 16, "16 tiles can't hold both halves growing to the last round");
    auto const choice = splitChoice(w);
    test::require(!P.closedRoomSplits || (choice.child == 0 && choice.held == "closedroom"),
                  "the child can't see the portal's far end: the closed-room gate holds the split");
}

void childThatSeesThePortalPairIsBred() {
    // The far end, (8, 3), is within the child's 7x7 view from (5, 4): it knows the way out at birth.
    auto w = portalRoom(8, 3);
    auto const choice = splitChoice(w);
    test::require(choice.child == P.childLength && choice.rule == "split" && choice.held == "none",
                  "a child that sees both ends of the portal walks out: the farmer keeps breeding");
}

void growthDecides() {
    auto w = portalRoom(12, 12);
    w.round = P.mergeRound - 20;
    test::require(need(w, 0) <= 16 && need(w, 1) > 16, "the room holds a dragon that stops eating, not one eating a pearl a round");
    test::require(splitChoice(w, 0).child == P.childLength, "no growth left to fit: the room holds both halves");
    auto const fast = splitChoice(w, 1);
    test::require(!P.closedRoomSplits || (fast.child == 0 && fast.held == "closedroom"),
                  "eating a pearl a round, the two halves would outgrow the room before the last round");
}

void teammateCrowdsTheRoom() {
    auto w = portalRoom(12, 12);
    w.round = P.mergeRound - 20;
    // Growth such that the room's 16 tiles hold both halves and its 12 left beside a 4-long teammate don't.
    double const rate = 3.0 / (unswbc::Constants::MAX_ROUNDS - 1 - w.round);
    test::require(need(w, rate) <= 16 && need(w, rate) > 12, "the rate puts the need between 12 and 16 tiles");
    test::require(splitChoice(w, rate).child == P.childLength, "alone in the room we split");
    // A teammate lies along y = 5, head west, out of the parent's way north.
    std::vector<int> mate{w.tileOf(2, 5), w.tileOf(3, 5), w.tileOf(4, 5), w.tileOf(5, 5)};
    test::dragon(w, 3, false, mate.front(), static_cast<int>(mate.size()));
    for (std::size_t i = 0; i < mate.size(); i++) {
        w.occId[mate[i]] = 3;
        w.occK[mate[i]] = static_cast<int>(mate.size() - 1 - i);
        w.touched.push_back(mate[i]);
    }
    auto const crowded = splitChoice(w, rate);
    test::require(!P.closedRoomSplits || (crowded.child == 0 && crowded.held == "closedroom"),
                  "a teammate's body takes room: the free tiles left can't hold both halves");
}

void openBoardIsUnchanged() {
    auto w = test::world();
    test::body(w, {w.tileOf(8, 8), w.tileOf(7, 8), w.tileOf(6, 8), w.tileOf(5, 8)});
    auto const choice = splitChoice(w);
    test::require(choice.child == P.childLength && choice.held == "none",
                  "open ground is a way out: the split goes ahead as before");
}

// A sealed 1-wide corridor (x = 8, y 2..13, 12 tiles) with pearls in its north end: a room with no way out. Our
// length-8 dragon heads north at (8, 4), tail at (8, 11), both ends dead: the trapped-tail shed's situation
// (shed.hpp).
bot::World sealedCorridor() {
    auto w = test::world();
    for (int y = 2; y <= 13; ++y) {
        test::wall(w, w.tileOf(8, y), 1);
        test::wall(w, w.tileOf(8, y), 3);
    }
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

void shedWaitsUntilTheRoomHoldsBoth() {
    auto w = sealedCorridor();
    auto const early = splitChoice(w);
    test::require(!P.closedRoomSplits || (early.child == 0 && early.held == "shed"),
                  "the shed child would be sealed in with us for the rest of the game: no shed");
    w.round = unswbc::Constants::MAX_ROUNDS - 10;
    test::require(need(w, 0) <= 12, "not eating, our 8 and two margins fit the 12 tiles");
    auto const late = splitChoice(w, 0);
    test::require(late.child == P.childLength && late.rule == "shed", "then the corridor holds both: we shed");
    // With P.roomByRoundsLeft, 2 rounds from the end the 2 tiles ahead hold our head to the last move (holdCap):
    // the pocket is open, so there is no trapped tail to shed.
    w.round = unswbc::Constants::MAX_ROUNDS - 2;
    test::require(!P.roomByRoundsLeft || splitChoice(w, 0).child == 0, "at the end the pocket holds us: no shed");
}

void unseenTailShedJudgesTheRoomFromTheHead() {
    auto w = sealedCorridor();
    // A split-out child's second turn: only the front half of its 12-long body is tracked (shed.hpp's shedAtBirth).
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
    auto const choice = splitChoice(w);
    test::require(!P.closedRoomSplits || (choice.child == 0 && choice.held == "shed"),
                  "the room around our head can't hold our 12 segments and a shed child: no shed at birth");
}

} // namespace

int main() {
    return test::suite({
        {"child without a way out is not bred", childWithoutAWayOutIsNotBred},
        {"child that sees the portal pair is bred", childThatSeesThePortalPairIsBred},
        {"growth to the last round decides", growthDecides},
        {"teammate crowds the room", teammateCrowdsTheRoom},
        {"open board is unchanged", openBoardIsUnchanged},
        {"shed waits until the room holds both", shedWaitsUntilTheRoomHoldsBoth},
        {"unseen tail: the room is judged from the head", unseenTailShedJudgesTheRoomFromTheHead},
    });
}
