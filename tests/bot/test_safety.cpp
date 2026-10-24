#include "support.hpp"
#include "safety.hpp"

namespace {

// Keeps each reachable region small enough to count by hand.
bot::World corridor(int cells) {
    auto w = test::world();
    w.setAllEdges(bot::EDGE_KELP);
    for (int x = 1; x < cells; ++x)
        w.setEdge(w.edgeOf(w.tileOf(x, 4), 1), bot::EDGE_OPEN);
    return w;
}

// Our next move closes a four-tile loop by entering the cell beside the tail.
bot::World tailLoop() {
    auto w = test::world();
    w.setAllEdges(bot::EDGE_KELP);
    test::body(w, {w.tileOf(1, 3), w.tileOf(2, 3), w.tileOf(2, 4)});
    for (auto [tile, dir] : {std::pair{w.tileOf(1, 3), 1}, {w.tileOf(2, 3), 2},
                             {w.tileOf(1, 4), 1}, {w.tileOf(1, 3), 2}})
        w.setEdge(w.edgeOf(tile, dir), bot::EDGE_OPEN);
    return w;
}

void closedCorridorHasNoFreedExit() {
    auto w = corridor(3);
    bot::Safety safety;
    safety.prepare(w);
    test::require(safety.floodFill(w.tileOf(1, 4), 0, 20) == 3,
                  "a three-cell corridor has exactly three reachable cells");
    test::require(!safety.reachedFreed, "empty cells do not provide a moving-tail escape");
}

void fillStopsAtItsCap() {
    auto w = corridor(6);
    bot::Safety safety;
    safety.prepare(w);
    test::require(safety.floodFill(w.tileOf(1, 4), 0, 3) == 3,
                  "a six-cell region must stop counting at the requested cap");
}

void ownTailOpensOnSecondStep() {
    auto w = tailLoop();
    bot::Safety safety;
    safety.prepare(w);
    test::require(safety.floodFill(w.tileOf(1, 4), 0, 20) == 4,
                  "tail, neck, and old head clear on steps two, three, and four");
    test::require(safety.reachedFreed, "following the tail keeps this small loop open");
}

void eatingFirstDelaysOwnTail() {
    auto w = tailLoop();
    test::pearl(w, w.tileOf(1, 4));
    bot::Safety safety;
    safety.prepare(w);
    test::require(safety.floodFill(w.tileOf(1, 4), 1, 20) == 1,
                  "growth leaves the tail occupied when we reach it on step two");
    test::require(!safety.reachedFreed, "the growth-blocked pocket has no freeing exit");
}

void pearlAlongPathAlsoDelaysBody() {
    auto w = test::world();
    w.setAllEdges(bot::EDGE_KELP);
    test::body(w, {w.tileOf(3, 3), w.tileOf(3, 4), w.tileOf(4, 4)});
    for (auto [tile, dir] : {std::pair{w.tileOf(2, 3), 1}, {w.tileOf(2, 3), 2},
                             {w.tileOf(2, 4), 1}, {w.tileOf(3, 3), 2}, {w.tileOf(3, 4), 1}})
        w.setEdge(w.edgeOf(tile, dir), bot::EDGE_OPEN);
    bot::Safety safety;
    safety.prepare(w);
    test::require(safety.floodFill(w.tileOf(2, 3), 0, 20) == 5,
                  "without growth the neck clears exactly when we reach it on step three");
    test::pearl(w, w.tileOf(2, 4));
    test::require(safety.floodFill(w.tileOf(2, 3), 0, 20) == 2,
                  "a pearl on step two delays the neck beyond our step-three arrival");
    test::require(!safety.reachedFreed, "each fill must reset the previous freed-tail result");
}

void staticFillCannotBorrowTailSpace() {
    auto w = tailLoop();
    bot::Safety safety;
    safety.prepare(w);
    test::require(safety.floodFill(w.tileOf(1, 4), 0, 20) == 4,
                  "normal movement can follow the tail");
    test::require(safety.floodFill(w.tileOf(1, 4), 0, 20, {.staticBodies = true}) == 1,
                  "a split cannot rely on any body segment moving away");
    test::require(!safety.reachedFreed, "static fills never report borrowing body tiles");
}

void visibleFillStopsAtUnseenEmptyTiles() {
    auto w = corridor(3);
    w.visStamp[w.tileOf(2, 4)] = 0;
    bot::Safety safety;
    safety.prepare(w);
    test::require(safety.floodFill(w.tileOf(1, 4), 0, 20) == 3,
                  "normal space estimation can use remembered open edges");
    test::require(safety.floodFill(w.tileOf(1, 4), 0, 20, {.visibleOnly = true}) == 1,
                  "pessimistic space must not cross an unseen empty cell");
}

void visibleFillKeepsTrackedOwnTail() {
    auto w = tailLoop();
    w.visStamp[w.tileOf(2, 4)] = 0;
    bot::Safety safety;
    safety.prepare(w);
    test::require(safety.floodFill(w.tileOf(1, 4), 0, 20, {.visibleOnly = true}) == 4,
                  "our remembered tail is known occupied even when outside vision");
    test::require(safety.reachedFreed, "a tracked hidden tail still supplies a timed escape");
}

void splitRivalWinsArrivalTies() {
    auto w = corridor(6);
    bot::DistField rival;
    rival.build(w, w.tileOf(6, 4), 20);
    bot::Safety safety;
    safety.prepare(w);
    test::require(safety.floodFill(w.tileOf(1, 4), 0, 20) == 6,
                  "the empty corridor contains six cells");
    test::require(safety.floodFill(w.tileOf(1, 4), 0, 20, {.rival = &rival}) == 2,
                  "cell three is an arrival tie and belongs to the other split head");
}

void otherHeadsReserveTheirTerritory() {
    for (bool enemy : {false, true}) {
        auto w = corridor(6);
        test::dragon(w, 2, enemy, w.tileOf(6, 4), 4);
        bot::Safety safety;
        safety.prepare(w);
        test::require(safety.floodFill(w.tileOf(1, 4), 0, 20) == 2,
                      "both teams' heads deny the tied third cell in a six-cell corridor");
        test::require(safety.floodFill(w.tileOf(1, 4), 0, 20, {.physical = true}) == 5,
                      "the physical fill counts contested cells and stops only at the other head");
    }
}

void otherTailNeedsGrowthMargin() {
    auto w = corridor(3);
    int const tail = w.tileOf(2, 4);
    w.occId[tail] = 2;
    w.occK[tail] = 0;
    w.touched.push_back(tail);
    bot::Safety safety;
    safety.prepare(w);
    test::require(safety.floodFill(w.tileOf(1, 4), 0, 20) == 1,
                  "another tail may grow and must not be entered on step two");

    w.occId[tail] = -1;
    w.occId[w.tileOf(3, 4)] = 2;
    w.occK[w.tileOf(3, 4)] = 0;
    w.touched.push_back(w.tileOf(3, 4));
    test::require(safety.floodFill(w.tileOf(1, 4), 0, 20) == 3,
                  "the same known tail is usable when first reached on step three");
    test::require(safety.reachedFreed, "a known other tail counts as a freeing exit");
}

void unknownOtherBodyNeverBecomesSpace() {
    auto w = corridor(4);
    w.occId[w.tileOf(3, 4)] = 2;
    w.touched.push_back(w.tileOf(3, 4));
    bot::Safety safety;
    safety.prepare(w);
    test::require(safety.floodFill(w.tileOf(1, 4), 0, 20) == 2,
                  "a body segment with unknown tail distance cannot be assumed to clear");
    test::require(!safety.reachedFreed, "an unknown body is a wall, not a tail escape");
}

void enemyReachRespectsWallsAndResetsNextTurn() {
    auto w = test::world();
    int const enemy = w.tileOf(4, 4);
    test::dragon(w, 2, true, enemy, 4);
    test::wall(w, enemy, 0);
    bot::Safety safety;
    safety.prepare(w);
    test::require(safety.enemyCanReach(w.tileOf(5, 4)), "an enemy threatens its open east exit");
    test::require(!safety.enemyCanReach(w.tileOf(4, 3)), "kelp prevents a northward enemy ram");
    ++w.stamp;
    ++w.round;
    w.dragons.clear();
    safety.prepare(w);
    test::require(!safety.enemyCanReach(w.tileOf(5, 4)), "last turn's enemy reach must expire");
    test::require(safety.otherHeadDist(w.tileOf(5, 4)) == bot::NEVER,
                  "last turn's territorial distances must expire too");
}

void tradeRequiresEnoughUnitsAndGain() {
    auto w = test::world();
    w.length = 6;
    w.unitCount = 3;
    bot::Safety safety;
    safety.prepare(w);
    test::require(safety.tradeGood(8, 2), "three units may trade six segments for eight");
    test::require(!safety.tradeGood(7, 2), "a one-segment gain cannot satisfy a two-segment minimum");
    w.unitCount = 2;
    safety.prepare(w);
    test::require(!safety.tradeGood(20, 0), "the last two units must be preserved even for a large gain");
}

void lateLongestDragonRefusesTrades() {
    auto w = test::world();
    w.length = 6;
    w.round = P.mergeRound - 1;
    bot::Safety safety;
    safety.prepare(w);
    test::require(safety.tradeGood(20, 0), "before the merge round our longest dragon may trade");
    w.round = P.mergeRound;
    safety.prepare(w);
    test::require(!safety.tradeGood(20, 0), "the late-game longest dragon must survive");
    test::dragon(w, 2, false, w.tileOf(1, 1), 7);
    safety.prepare(w);
    test::require(safety.tradeGood(20, 0), "a smaller teammate is not the protected longest dragon");
    w.dragons.back().visibleLen = 6;
    safety.prepare(w);
    test::require(!safety.tradeGood(20, 0), "ties for longest must also be protected");
}

void oneBadEnemyTradeMakesTileRisky() {
    auto w = test::world();
    int const destination = w.tileOf(8, 7);
    test::dragon(w, w.myId, false, w.head, w.length);
    test::dragon(w, 2, true, w.tileOf(8, 6), 6);
    bot::Safety safety;
    safety.prepare(w);
    test::require(!safety.riskyFor(destination), "a larger enemy alone offers an acceptable trade");
    test::dragon(w, 3, true, w.tileOf(7, 7), 3);
    test::dragon(w, 4, false, w.tileOf(1, 1), 4); // keep head counts tied
    safety.prepare(w);
    test::require(safety.riskyFor(destination), "one smaller enemy makes the shared destination unsafe");
}

// P.pricedGuard: the ram guard prices an enemy head's ram on us as our own ram gate would from its side. Our length-3
// dragon at (8,8), body west, would step north to (8,7), beside an equal enemy head at (8,6).
bot::World besideEqualHead() {
    auto w = test::world();
    test::body(w, {w.tileOf(8, 8), w.tileOf(7, 8), w.tileOf(6, 8)});
    test::dragon(w, 2, true, w.tileOf(8, 6), 3);
    return w;
}

bool risky(bot::World const& w, int t) {
    bot::Safety safety;
    safety.prepare(w);
    return safety.riskyFor(t);
}

void equalHeadRiskyWhenItsSideEatsTheDrop() {
    auto w = besideEqualHead();
    int const t = w.tileOf(8, 7);
    test::require(!risky(w, t), "one against one, nobody else near the drop: the equal head gains nothing by ramming");
    test::dragon(w, 3, true, w.tileOf(9, 5), 2);
    test::dragon(w, 5, false, w.tileOf(14, 14), 2); // tied head counts in view, too far to eat any of the drop
    test::require(risky(w, t), "their other head reaches the drop first: the equal head's ram pays it, even at a tie");
    test::dragon(w, 4, false, w.tileOf(7, 9), 2);
    test::require(!risky(w, t), "our teammate reaches as much of the drop first: not worth its ram");
}

void pearlMakesEqualHeadShorter() {
    auto w = besideEqualHead();
    int const t = w.tileOf(8, 7);
    test::pearl(w, t);
    test::require(risky(w, t), "eating the pearl leaves the head one shorter than us: its ram gains a segment");
}

void shorterHeadRiskyUnlessOurHeadsCrowdIt() {
    auto w = test::world();
    test::body(w, {w.tileOf(8, 8), w.tileOf(7, 8), w.tileOf(6, 8), w.tileOf(5, 8)});
    test::dragon(w, 2, true, w.tileOf(8, 6), 3);
    int const t = w.tileOf(8, 7);
    test::require(risky(w, t), "a head one shorter gains a segment by ramming us at a tie");
    test::dragon(w, 3, false, w.tileOf(10, 7), 2);
    test::dragon(w, 4, false, w.tileOf(6, 6), 2);
    test::require(!risky(w, t), "two of our heads beside the collision eat its drop: the shorter head's ram doesn't pay");
    w.length = 3 - P.ramModelMinGain + 1; // the head is now more than -P.ramModelMinGain shorter than us
    test::dragon(w, 5, false, w.tileOf(9, 9), 2);
    test::dragon(w, 6, false, w.tileOf(7, 5), 2);
    test::require(risky(w, t), "a head beyond the ram model's range is a bad trade for us however many heads we have near");
}

void longerHeadRiskyWhenItsTeamIsNear() {
    auto w = test::world();
    test::body(w, {w.tileOf(8, 8), w.tileOf(7, 8), w.tileOf(6, 8)});
    test::dragon(w, 2, true, w.tileOf(8, 6), 4);
    test::dragon(w, 3, true, w.tileOf(14, 14), 2);
    int const t = w.tileOf(8, 7);
    test::require(!risky(w, t), "outnumbered in view, a head one longer with its teammate far away loses by ramming us");
    w.dragons.back().head = w.tileOf(9, 5);
    test::require(risky(w, t), "with its teammate beside the collision, the longer head's ram pays");
}

} // namespace

int main() {
    return test::suite({
        {"closed corridor has no freed exit", closedCorridorHasNoFreedExit},
        {"fill stops at its cap", fillStopsAtItsCap},
        {"own tail opens on step two", ownTailOpensOnSecondStep},
        {"eating first delays own tail", eatingFirstDelaysOwnTail},
        {"path pearl delays body", pearlAlongPathAlsoDelaysBody},
        {"static fill cannot borrow body space", staticFillCannotBorrowTailSpace},
        {"visible fill rejects unseen empty cells", visibleFillStopsAtUnseenEmptyTiles},
        {"visible fill keeps tracked own tail", visibleFillKeepsTrackedOwnTail},
        {"split rival wins arrival ties", splitRivalWinsArrivalTies},
        {"other heads reserve territory", otherHeadsReserveTheirTerritory},
        {"other tail needs growth margin", otherTailNeedsGrowthMargin},
        {"unknown body never becomes space", unknownOtherBodyNeverBecomesSpace},
        {"enemy reach and territory reset", enemyReachRespectsWallsAndResetsNextTurn},
        {"trade needs units and gain", tradeRequiresEnoughUnitsAndGain},
        {"late longest dragon refuses trades", lateLongestDragonRefusesTrades},
        {"one bad trade makes a tile risky", oneBadEnemyTradeMakesTileRisky},
        {"priced guard: equal head by who eats the drop", equalHeadRiskyWhenItsSideEatsTheDrop},
        {"priced guard: eating makes an equal head shorter", pearlMakesEqualHeadShorter},
        {"priced guard: shorter head unless our heads crowd it", shorterHeadRiskyUnlessOurHeadsCrowdIt},
        {"priced guard: longer head when its team is near", longerHeadRiskyWhenItsTeamIsNear},
    });
}
