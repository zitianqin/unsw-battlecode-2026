#include "support.hpp"
#include "scoring.hpp"
#include "feed.hpp"
#include "split.hpp"

#include <algorithm>
#include <cstdlib>
#include <string>

namespace {

bot::World straightDragon(int width = 16, int height = 16) {
    auto w = test::world(width, height);
    test::body(w, {w.tileOf(8, 8), w.tileOf(7, 8), w.tileOf(6, 8), w.tileOf(5, 8)});
    return w;
}

unswbc::Controller controllerFor(bot::World const& w) {
    unswbc::Controller controller(w.myId, unswbc::Team::A, unswbc::Direction::EAST, {}, w.unitLimit);
    controller.length = w.length;
    controller.unit_count = w.unitCount;
    controller.head.position = {w.xOf(w.head), w.yOf(w.head)};
    return controller;
}

int wantsSplit(bot::World const& w) {
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    auto const best = bot::bestMove(w, safety, targets);
    return bot::shouldSplit(w, controllerFor(w), safety, best);
}

// The rule shouldSplit names for main's debug log line, or "none" when it doesn't split. Naming it must not
// change the split: the child length matches the call without a name.
std::string splitRule(bot::World const& w) {
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    auto const best = bot::bestMove(w, safety, targets);
    char const* rule = "none";
    int const child = bot::shouldSplit(w, controllerFor(w), safety, best, &rule);
    test::require(child == wantsSplit(w), "naming the rule changes the split");
    return rule;
}

void avoidsWallBodyAndFriendlyHead() {
    auto w = straightDragon();
    test::wall(w, w.head, 0);
    test::dragon(w, 2, false, w.tileOf(8, 9), 4);
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    auto const best = bot::bestMove(w, safety, targets);
    test::require(best.dir == 1 && best.dest == w.tileOf(9, 8),
                  "east is the only move without kelp, our body, or a friendly head");
    for (int blocked : {0, 2, 3})
        test::require(bot::evaluateMove(w, safety, targets, blocked).score < best.score,
                      "each collision must rank below the safe east move");
}

void trappedDragonHitsWallRatherThanTeammate() {
    auto w = straightDragon();
    for (int dir : {0, 2})
        test::wall(w, w.head, dir);
    test::dragon(w, 2, false, w.tileOf(9, 8), 4);
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    test::require(bot::bestMove(w, safety, targets).dir != 1,
                  "with every move fatal, dying alone beats a head-on that also kills the teammate");
}

void takesPearlAmongSafeMoves() {
    auto w = straightDragon();
    test::pearl(w, w.tileOf(9, 8));
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    test::require(bot::bestMove(w, safety, targets).dir == 1,
                  "an immediately edible pearl should win among otherwise open moves");
}

// Equally good moves keep our heading; with the way ahead shut they fall back to N, E, S, W order.
void tiedSafeMovesKeepHeading() {
    auto w = straightDragon();
    w.facing[w.head] = 1;
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    test::require(bot::bestMove(w, safety, targets).dir == 1, "equally safe north, east and south: go straight on east");
    test::require(bot::bestOf(bot::evaluateMoves(w, safety, targets)).dir == 0,
                  "without a heading the tie keeps N, E, S, W order");
    test::wall(w, w.head, 1);
    safety.prepare(w);
    test::require(bot::bestMove(w, safety, targets).dir == 0, "east shut, north and south tie: north by fixed order");
}

// A target diagonally ahead: both moves towards it tie, so we keep our heading instead of turning north.
void diagonalTargetKeepsHeadingUnlessAMoveIsBetter() {
    auto w = straightDragon();
    w.facing[w.head] = 1;
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    targets.explore = w.tileOf(12, 4);
    targets.exploreField.build(w, targets.explore, 30);
    test::require(bot::bestMove(w, safety, targets).dir == 1, "north-east target, facing east: east");
    targets.explore = w.tileOf(8, 4);
    targets.exploreField.build(w, targets.explore, 30);
    test::require(bot::bestMove(w, safety, targets).dir == 0, "a target due north: the strictly shorter north move wins");
}

void avoidsUnknownPortalWhenSafeExitExists() {
    auto w = straightDragon();
    w.setEdge(w.edgeOf(w.head, 0), bot::EDGE_PORTAL, 7);
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    test::require(bot::bestMove(w, safety, targets).dir == 1,
                  "the first known safe exit beats a portal with an unseen partner");
}

void rejectsPearlInClosedPocket() {
    auto w = straightDragon();
    int const pearl = w.tileOf(9, 8);
    test::pearl(w, pearl);
    for (int dir : {0, 1, 2})
        test::wall(w, pearl, dir);
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    auto const pocket = bot::evaluateMove(w, safety, targets, 1);
    test::require(pocket.closed && pocket.space == 1,
                  "the pearl's only exit is our head, which cannot clear on step two");
    test::require(bot::bestMove(w, safety, targets).dir == 0,
                  "an open north move must beat a fatal one-cell pearl pocket");
}

// A dragon of `length` lying west from (8,8), facing east into a 1-wide corridor (9..11,8) walled north and
// south. Only our 7x7 view is visible, so the corridor's far end (x >= 12) is out of sight; `closedInView`
// walls it off at x = 11.
bot::World dragonBesideCorridor(int length, bool closedInView) {
    auto w = test::world();
    std::vector<int> body;
    for (int i = 0; i < length; ++i)
        body.push_back(w.tileOf(8 - i, 8));
    test::body(w, body);
    for (int x = 9; x <= 11; ++x) {
        test::wall(w, w.tileOf(x, 8), 0);
        test::wall(w, w.tileOf(x, 8), 2);
    }
    if (closedInView)
        test::wall(w, w.tileOf(11, 8), 1);
    for (int t = 0; t < w.N; ++t)
        if (std::abs(w.xOf(t) - 8) > 3 || std::abs(w.yOf(t) - 8) > 3)
            w.visStamp[t] = 0;
    return w;
}

void eatsPearlAtBlindCorridorMouth() {
    auto w = dragonBesideCorridor(2, false);
    int const pearl = w.tileOf(9, 8);
    test::pearl(w, pearl);
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    targets.pearl = pearl;
    targets.pearlField.build(w, pearl, 30);
    test::require(!bot::evaluateMove(w, safety, targets, 1).closed,
                  "a corridor whose far end is out of sight is not a known dead end");
    test::require(bot::bestMove(w, safety, targets).dir == 1,
                  "the pearl target on a blind corridor's first tile beats walking away from it");
}

void refusesPearlInCorridorClosedInView() {
    auto w = dragonBesideCorridor(2, true);
    int const pearl = w.tileOf(9, 8);
    test::pearl(w, pearl);
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    targets.pearl = pearl;
    targets.pearlField.build(w, pearl, 30);
    test::require(bot::evaluateMove(w, safety, targets, 1).closed, "a dead end in view is a closed pocket");
    test::require(bot::bestMove(w, safety, targets).dir != 1,
                  "the pearl target must not lure a short dragon into a dead end it can see");
}

void blindCorridorLosesTieToOpenMove() {
    auto w = dragonBesideCorridor(2, false);
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    targets.explore = w.tileOf(13, 12);
    targets.exploreField.build(w, targets.explore, 30);
    test::require(targets.exploreField.at(w.tileOf(9, 8)) == targets.exploreField.at(w.tileOf(8, 9)),
                  "east through the corridor and south over open ground are equally far from the target");
    test::require(bot::bestMove(w, safety, targets).dir == 2,
                  "at equal explore distance the open move beats the corridor we cannot see the end of");
}

void longDragonFollowsExploreThroughBlindCorridor() {
    auto w = dragonBesideCorridor(6, false);
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    targets.explore = w.tileOf(14, 8);
    targets.exploreField.build(w, targets.explore, 30);
    test::require(targets.exploreField.at(w.tileOf(9, 8)) + 2 == targets.exploreField.at(w.tileOf(8, 7)),
                  "the corridor is two steps shorter to the explore target than going round it");
    test::require(bot::bestMove(w, safety, targets).dir == 1,
                  "a six-long dragon still takes the shorter way through a corridor it cannot see the end of");
}

void avoidsPearlAnEnemyCanRam() {
    auto w = straightDragon();
    test::pearl(w, w.tileOf(9, 8));
    test::dragon(w, 2, true, w.tileOf(10, 8), 2);
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    test::require(bot::bestMove(w, safety, targets).dir == 0,
                  "a pearl must not lure four segments into a possible two-segment enemy ram");
}

// Our length-3 dragon at (8,8), body west, with a pearl north at (8,7) that the length-4 enemy head at (8,6)
// can step onto next: as long as us once we eat it. A second enemy head (length 5) is in view, 7 steps from that head:
// it reaches the head-on's drop before any head of ours.
bot::World outnumberedBesidePearl() {
    auto w = test::world();
    test::body(w, {w.tileOf(8, 8), w.tileOf(7, 8), w.tileOf(6, 8)});
    test::pearl(w, w.tileOf(8, 7));
    test::dragon(w, 2, true, w.tileOf(8, 6), 4);
    test::dragon(w, 3, true, w.tileOf(12, 3), 5);
    return w;
}

bot::MoveEval bestFor(bot::World const& w) {
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    return bot::bestMove(w, safety, targets);
}

// P.pricedGuard: the equal head's ram on us pays it when its side eats the drop (Safety::badRammer).
void outnumberedAvoidsTileBesideEqualHead() {
    auto w = outnumberedBesidePearl();
    auto const best = bestFor(w);
    test::require(best.dest != w.tileOf(8, 7) && best.score > P.doomedScore,
                  "their other head reaches the drop first: the pearl beside an equal enemy head is left for an open move");
    auto longer = outnumberedBesidePearl();
    longer.dragons[1].visibleLen = 6;
    longer.dragons[2].visibleLen = 3;
    test::require(bestFor(longer).dest == longer.tileOf(8, 7),
                  "a head two longer loses by ramming us while its teammate is away from the collision: the pearl is eaten");
}

void teammateNearDropKeepsTileBesideEqualHead() {
    auto w = outnumberedBesidePearl();
    test::dragon(w, 4, false, w.tileOf(7, 9), 3);
    test::require(bestFor(w).dest == w.tileOf(8, 7), "our teammate reaches the drop first: the pearl is eaten");
}

void tiedCountsKeepTileBesideEqualHead() {
    auto w = outnumberedBesidePearl();
    w.dragons.pop_back();
    test::require(bestFor(w).dest == w.tileOf(8, 7), "one against one: the pearl beside the equal head is eaten");
}

void outnumberedAndSurroundedStillMoves() {
    auto w = test::world();
    test::body(w, {w.tileOf(8, 8), w.tileOf(7, 8), w.tileOf(6, 8)});
    test::dragon(w, 2, true, w.tileOf(8, 6), 3);
    test::dragon(w, 3, true, w.tileOf(10, 8), 3);
    test::dragon(w, 4, true, w.tileOf(8, 10), 5);
    auto const best = bestFor(w);
    test::require(best.dest == w.tileOf(8, 7) || best.dest == w.tileOf(9, 8) || best.dest == w.tileOf(8, 9),
                  "with every open move marked risky the dragon still steps onto an empty tile");
    test::require(!best.safe && best.score > P.doomedScore, "every open move is risky, but none is doomed: no split-out");
}

// A teammate head at (10,8) walled north, east and south can only leave through (9,8), the pearl east of us
// that is our target (so it wins over the open north and south moves when nothing else stops it).
bot::World teammateBesideOnlyExit(bool secondExit) {
    auto w = straightDragon();
    int const mate = w.tileOf(10, 8);
    test::dragon(w, 2, false, mate, 4);
    for (int dir : {0, 1, 2})
        if (!(secondExit && dir == 1))
            test::wall(w, mate, dir);
    test::pearl(w, w.tileOf(9, 8));
    return w;
}

bot::Targets pearlTarget(bot::World const& w) {
    bot::Targets targets;
    targets.pearl = w.tileOf(9, 8);
    targets.pearlField.build(w, targets.pearl, 20);
    return targets;
}

void leavesTeammateItsOnlyExit() {
    auto w = teammateBesideOnlyExit(false);
    bot::Safety safety;
    safety.prepare(w);
    auto const targets = pearlTarget(w);
    test::require(!bot::evaluateMove(w, safety, targets, 1).safe, "a visible teammate's only exit is not a safe move");
    test::require(bot::bestMove(w, safety, targets).dir != 1,
                  "the pearl on a teammate's only exit is left so the teammate can get out");
}

void takesTileWhenTeammateHasAnotherExit() {
    auto w = teammateBesideOnlyExit(true);
    bot::Safety safety;
    safety.prepare(w);
    auto const targets = pearlTarget(w);
    test::require(bot::bestMove(w, safety, targets).dir == 1,
                  "with the teammate's east side open, the pearl beside it is eaten");
}

void spreadsOutFromFriendlyHeads() {
    auto w = straightDragon();
    test::dragon(w, 2, false, w.tileOf(9, 6), 4);
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    test::require(bot::bestMove(w, safety, targets).dir == 2,
                  "equally open moves should leave room by moving south away from a nearby teammate");
}

void takesFavorableHeadOnTrade() {
    auto w = straightDragon();
    test::dragon(w, 2, true, w.tileOf(9, 8), 8);
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    test::require(bot::bestMove(w, safety, targets).dir == 1,
                  "with four units, trading our four segments for eight is worthwhile");
}

void preservesLastUnitsInsteadOfAttacking() {
    auto w = straightDragon();
    w.unitCount = 2;
    test::dragon(w, 2, true, w.tileOf(9, 8), 8);
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    test::require(bot::bestMove(w, safety, targets).dir == 0,
                  "a team with only two units takes a safe exit instead of a head-on trade");
}

void protectedKingDoesNotAttack() {
    auto w = test::world();
    test::body(w, {w.tileOf(8, 8), w.tileOf(7, 8), w.tileOf(6, 8),
                   w.tileOf(5, 8), w.tileOf(4, 8), w.tileOf(3, 8)});
    w.round = P.mergeRound + 1;
    test::dragon(w, 2, true, w.tileOf(9, 8), 10);
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    test::require(bot::bestMove(w, safety, targets).dir == 0,
                  "our late-game longest dragon preserves its length instead of trading up");
}

void waitsBesideFutureSpawnUntilReady() {
    auto w = straightDragon();
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    targets.pearl = w.tileOf(9, 8);
    targets.pearlField.build(w, targets.pearl, 20);
    targets.pearlReady = 3;
    test::require(bot::bestMove(w, safety, targets).dir == 0,
                  "arriving early would occupy the spawn tile and suppress its pearl");
    targets.pearlReady = 1;
    test::require(bot::bestMove(w, safety, targets).dir == 1,
                  "once ready, the adjacent spawn target should attract the move");
}

void followsExplorationTargetWithoutPearls() {
    auto w = straightDragon();
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    targets.explore = w.tileOf(10, 8);
    targets.exploreField.build(w, targets.explore, 20);
    test::require(bot::bestMove(w, safety, targets).dir == 1,
                  "without a pearl target, the safe east move approaches the exploration target");
}

void openBoardAllowsLegalSplit() {
    auto w = straightDragon();
    test::require(wantsSplit(w), "a four-segment dragon with room for both halves should split");
}

void splitStopsAtEndgameBoundary() {
    auto w = straightDragon();
    w.round = P.mergeRound - 1;
    test::require(wantsSplit(w), "a safe split is still useful immediately before the merge round");
    w.round = P.mergeRound;
    test::require(!wantsSplit(w), "the merge round stops ordinary splits");
}

// The fold is one phase: splits stop, the longest dragon turns king and merges begin on the same round, so a
// swarm keeps splitting right up to it (stopping splits early without merging lost the gain against swarms).
void foldPartsStartTogether() {
    auto w = test::world();
    w.myId = 5;
    test::body(w, {w.tileOf(8, 8), w.tileOf(7, 8), w.tileOf(6, 8), w.tileOf(5, 8), w.tileOf(4, 8), w.tileOf(3, 8)});
    w.unitCount = 30;
    test::dragon(w, 2, false, w.tileOf(10, 8), 6);
    bot::DistField field;
    bot::Safety safety;

    w.round = P.mergeRound - 1;
    safety.prepare(w);
    test::require(wantsSplit(w) == P.childLength && bot::feedDirection(w, field) < 0 && !safety.protectedDragon,
                  "one round before the fold we still split, nobody merges and nobody is king");
    w.round = P.mergeRound;
    safety.prepare(w);
    test::require(wantsSplit(w) == 0 && bot::feedDirection(w, field) >= 0 && safety.protectedDragon,
                  "on the fold round splits stop, we merge into the equal lower-id teammate and are king-eligible");
}

void splitNeedsEnoughSegmentsForBothHalves() {
    auto w = test::world();
    test::body(w, {w.tileOf(8, 8), w.tileOf(7, 8), w.tileOf(6, 8)});
    test::require(!wantsSplit(w), "three segments cannot make two legal two-segment dragons");
}

void splitHonorsTeamUnitCap() {
    auto w = straightDragon();
    int const lastFree = w.unitLimit - bot::rescueReserve(w) - 1;
    w.unitCount = lastFree;
    test::require(wantsSplit(w), "the last slot outside the rescue reserve permits a safe split");
    w.unitCount = w.unitLimit;
    test::require(!wantsSplit(w), "a full team cannot add a child");
}

// The slots P.shedReserve keeps on every map in `round` for sheds under threat (split.hpp's shedLength).
int threatShedSlots(int round) {
    return P.shedUnderThreat && (round < bot::threatShedEnd() || P.longShedsInFold) ? P.shedReserve : 0;
}

// On a big map the ordinary split leaves P.rescueSlots unit slots free (rescueReserve; more if P.shedReserve asks)
// from P.rescueFrom until P.mergeRound, so a trapped long dragon can still split out at the cap; below the reserve,
// splits run as before.
void ordinarySplitKeepsRescueSlots() {
    auto w = straightDragon(64, 64);
    w.round = P.rescueFrom;
    int const reserve = std::max(P.rescueSlots, threatShedSlots(w.round));
    test::require(P.reserveRescueSlots && bot::rescueReserve(w) == reserve, "the reserve holds from rescueFrom");
    int const reserveFull = w.unitLimit - reserve;
    w.unitCount = reserveFull + 1;
    test::require(!wantsSplit(w), "inside the rescue reserve an open-ground dragon does not split");
    w.unitCount = reserveFull;
    test::require(!wantsSplit(w), "taking the first reserved slot is refused too");
    w.unitCount = reserveFull - 1;
    test::require(wantsSplit(w) == P.childLength, "below the reserve the ordinary split runs as before");
    w.round = P.mergeRound;
    test::require(bot::rescueReserve(w) == threatShedSlots(w.round),
                  "from the merge round ordinary splits stop, and the rescue slots with them");
    if (P.rescueFrom > 0) {
        w.round = P.rescueFrom - 1;
        w.unitCount = reserveFull + 1;
        test::require(wantsSplit(w) == P.childLength, "before the rescue round every slot is still open to splits");
    }
}

// A dragon `length` long filling a walled corridor from (6, 12) east to its dead end, head at the dead end.
bot::World doomedLongInCorridor(int size = 24, int length = 12) {
    auto w = test::world(size, size);
    std::vector<int> tiles;
    int const end = 5 + length;
    for (int x = end; x >= 6; --x) {
        tiles.push_back(w.tileOf(x, 12));
        test::wall(w, w.tileOf(x, 12), 0);
        test::wall(w, w.tileOf(x, 12), 2);
    }
    test::body(w, tiles);
    test::wall(w, w.tileOf(end, 12), 1);
    w.round = P.rescueFrom + 70;
    return w;
}

// The reserve's point: on a big map a doomed long dragon splits out into a reserved slot, down to the last one (as
// a second doomed dragon in the same round would, after the first one's child); only a full team can't. Split-outs
// were never gated by the reserve; this pins that they stay so.
void doomedLongDragonSplitsOutIntoRescueSlot() {
    auto w = doomedLongInCorridor(64);
    test::require(bot::rescueReserve(w) == std::max(P.rescueSlots, threatShedSlots(w.round)),
                  "on a 64 x 64 board the reserve holds");
    int const child = w.length - unswbc::Constants::MIN_SIZE;
    w.unitCount = w.unitLimit - P.rescueSlots;
    test::require(wantsSplit(w) == child, "a length-12 dragon with every move fatal splits its tail out into a reserved slot");
    w.unitCount = w.unitLimit - 1;
    test::require(wantsSplit(w) == child, "the last reserved slot still takes a split-out");
    w.unitCount = w.unitLimit;
    test::require(wantsSplit(w) == 0, "a full team cannot split out");
}

// The reserve holds only on maps of at least P.reserveMinArea tiles, where the cap binds and games end on length;
// on smaller maps, which end in eliminations, every slot stays open to ordinary splits.
void rescueReserveOnlyOnBigMaps() {
    test::require(32 * 32 < P.reserveMinArea && P.reserveMinArea <= 48 * 24,
                  "premise: 32 x 32 boards are small and 48 x 24 ones big");
    int const reserveFull = 64 - P.rescueSlots; // test boards' unit limit is 64

    // A 64 x 64 map with the team at the reserve: a doomed 20-long dragon splits out, while a length-4 dragon in
    // open ground makes no ordinary split.
    auto doomed = doomedLongInCorridor(64, 20);
    doomed.unitCount = reserveFull;
    test::require(wantsSplit(doomed) == 20 - unswbc::Constants::MIN_SIZE, "the doomed 20-long dragon splits out");
    auto open = straightDragon(64, 64);
    open.round = P.rescueFrom;
    open.unitCount = reserveFull;
    test::require(!wantsSplit(open), "on a 64 x 64 map the length-4 dragon leaves the reserved slots free");

    // 32 x 32 (1024 tiles): the length-4 dragon splits into the same slot, as the champion always did.
    auto small = straightDragon(32, 32);
    small.round = P.rescueFrom;
    small.unitCount = reserveFull;
    test::require(bot::rescueReserve(small) == threatShedSlots(small.round) &&
                      wantsSplit(small) == (threatShedSlots(small.round) < P.rescueSlots ? P.childLength : 0),
                  "on a 32 x 32 map every slot but the shed reserve is open to ordinary splits");

    // 48 x 24 (1152 tiles, Stronghold's size): the reserve applies.
    auto edge = straightDragon(48, 24);
    edge.round = P.rescueFrom;
    edge.unitCount = reserveFull;
    test::require(bot::rescueReserve(edge) == std::max(P.rescueSlots, threatShedSlots(edge.round)) && !wantsSplit(edge),
                  "on a 48 x 24 map the reserve holds");

    // The boundary, stated through the parameter.
    auto at = test::world(P.reserveMinArea, 1), under = test::world(P.reserveMinArea - 1, 1);
    at.round = under.round = P.rescueFrom;
    test::require(bot::rescueReserve(at) == std::max(P.rescueSlots, threatShedSlots(at.round)) &&
                      bot::rescueReserve(under) == threatShedSlots(under.round),
                  "exactly reserveMinArea tiles keeps the reserve, one tile fewer doesn't");
}

// A dragon of `length` lying west from (8, 8) on an open 16x16 board, to test a split gate at and past P.splitAtLength
// (P.splitSpaceGates holds only the longer one).
bot::World straightDragonOf(int length) {
    auto w = test::world(16, 16);
    std::vector<int> body;
    for (int i = 0; i < length; i++)
        body.push_back(w.tileOf(8 - i, 8));
    test::body(w, body);
    return w;
}

void splitNeedsKnownTail() {
    auto w = straightDragon();
    w.body.pop_back();
    test::require(wantsSplit(w) == (P.splitSpaceGates || !P.splitUntrackedTail ? 0 : P.childLength),
                  "at the split length a dragon splits blind unless the space gates hold it");
    w = straightDragonOf(P.splitAtLength + 1);
    w.body.pop_back();
    test::require(!wantsSplit(w), "a longer dragon without the tail position cannot check the child's exits");
}

void splitAvoidsAdjacentEnemyHead() {
    auto w = straightDragon();
    test::dragon(w, 2, true, w.tileOf(8, 9), 2);
    test::require(!wantsSplit(w), "an adjacent enemy can ram the parent while it stands still to split");
}

void closedParentPocketHoldsOnlyLongerSplits() {
    auto w = straightDragon();
    for (int dir : {0, 2})
        test::wall(w, w.head, dir);
    for (int dir : {0, 1, 2})
        test::wall(w, w.tileOf(9, 8), dir);
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    auto const best = bot::bestMove(w, safety, targets);
    test::require(best.closed && best.space == 1, "the parent's only nonlethal move is a one-cell pocket");
    test::require(bot::shouldSplit(w, controllerFor(w), safety, best) == (P.splitSpaceGates ? 0 : P.childLength),
                  "at the split length the parent's space gates hold the split only with P.splitSpaceGates");
    w = straightDragonOf(P.splitAtLength + 1);
    for (int dir : {0, 2})
        test::wall(w, w.head, dir);
    for (int dir : {0, 1, 2})
        test::wall(w, w.tileOf(9, 8), dir);
    safety.prepare(w);
    auto const longer = bot::bestMove(w, safety, targets);
    test::require(longer.closed && !bot::shouldSplit(w, controllerFor(w), safety, longer),
                  "a longer parent without a viable move keeps its gate: no child");
}

void splitInRingMeetsParentSpaceGate() {
    auto w = straightDragon();
    for (int x = 0; x < 16; ++x) {
        test::wall(w, w.tileOf(x, 8), 0);
        test::wall(w, w.tileOf(x, 8), 2);
    }
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    auto const best = bot::bestMove(w, safety, targets);
    test::require(!best.closed && best.space == 16,
                  "moving without a split can follow our tail around the sixteen-cell ring");
    // The sealed ring is a closed room: its 16 tiles can't hold both halves growing for the rest of the game
    // (roomsplit.hpp). In the last rounds before P.mergeRound they can, and the parent space gate decides.
    test::require(!P.closedRoomSplits || bot::shouldSplit(w, controllerFor(w), safety, best) == 0,
                  "early in the game the closed-room gate keeps the ring from filling with our dragons");
    w.round = P.mergeRound - 1;
    // With our body standing and the child's head (x 5) winning ties, the parent keeps x 9..14 of the ring.
    int const parentNeed = std::max(w.length - P.childLength + P.spaceMargin, P.splitParentSpace);
    test::require(bot::shouldSplit(w, controllerFor(w), safety, best) == (parentNeed <= 6 ? P.childLength : 0),
                  "the parent splits in the ring only if its six-tile share meets the parent space gate");
}

void splitRequiresAnUnoccupiedChildExit() {
    auto w = straightDragon();
    for (int dir : {0, 2, 3})
        test::wall(w, w.body.back(), dir);
    test::require(!wantsSplit(w), "the child cannot exit through kelp or its standing parent body");
}

// A newborn child split out of a dead end: only its 7x7 view has ever been seen, and its tail touches its parent's
// body just past the view, which reads as an empty tile (World::observe). The ordinary split must not hand that
// tile to a grandchild as its exit (P.splitExitMustBeSeen); exits beside the tail, in view, still count.
void splitChildExitMustHaveBeenSeen() {
    auto newborn = [] {
        auto w = straightDragon();
        for (int t = 0; t < w.N; t++) {
            if (std::max(std::abs(w.xOf(t) - 8), std::abs(w.yOf(t) - 8)) > 3) {
                w.visStamp[t] = 0;
                w.lastSeen[t] = -1;
            }
        }
        return w;
    };
    auto w = newborn();
    test::require(wantsSplit(w) == P.childLength, "a newborn in the open splits: the tail's side exits are in view");
    for (int dir : {0, 2})
        test::wall(w, w.body.back(), dir);
    int const behindTail = w.tileOf(4, 8);
    test::require(!w.visible(behindTail) && w.occId[behindTail] < 0, "the tile behind the tail is out of view");
    test::require(wantsSplit(w) == (P.splitExitMustBeSeen ? 0 : P.childLength),
                  "the tail's only free exit was never seen: it may be the parent's body, so no split");
    w.lastSeen[behindTail] = w.round - 1;
    test::require(wantsSplit(w) == P.childLength, "the same exit, seen on an earlier turn, lets the split go");
}

void splitIntoSmallChildPocketFollowsChildGate() {
    auto w = straightDragon();
    for (int x = 2; x <= 5; ++x) {
        test::wall(w, w.tileOf(x, 8), 0);
        test::wall(w, w.tileOf(x, 8), 2);
    }
    test::wall(w, w.tileOf(2, 8), 3);
    test::require(wantsSplit(w) == (P.splitChildSpace <= 3 ? P.childLength : 0),
                  "the child's only exit reaches three cells: we split only if the child space gate allows it");
}

// A length-3 dragon in open ground, with two teammate heads two and three steps from its head.
bot::World crowdedOpenGround() {
    auto w = test::world(24, 24);
    test::body(w, {w.tileOf(12, 12), w.tileOf(11, 12), w.tileOf(10, 12)});
    test::dragon(w, 2, false, w.tileOf(12, 10), 3);
    test::dragon(w, 3, false, w.tileOf(14, 13), 3);
    return w;
}

void contestedOpenGroundStillFollowsPearl() {
    auto w = crowdedOpenGround();
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    targets.pearl = w.tileOf(16, 12);
    targets.pearlField.build(w, targets.pearl, 30);
    auto const east = bot::evaluateMove(w, safety, targets, 1);
    test::require(!east.closed && east.space < bot::evaluateMove(w, safety, targets, 2).space,
                  "teammates reach east's tiles first, but open ground is not a closed pocket");
    test::require(bot::bestMove(w, safety, targets).dir == 1,
                  "the move toward the pearl beats the move with the larger uncontested share");
}

void deadEndCorridorIsStillRefused() {
    auto w = crowdedOpenGround();
    for (int x = 13; x <= 17; ++x) {
        test::wall(w, w.tileOf(x, 12), 0);
        test::wall(w, w.tileOf(x, 12), 2);
    }
    test::wall(w, w.tileOf(17, 12), 1);
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    targets.explore = w.tileOf(17, 12);
    targets.exploreField.build(w, targets.explore, 30);
    auto const east = bot::evaluateMove(w, safety, targets, 1);
    test::require(east.closed && east.room == 5, "a five-cell dead-end corridor is physically closed");
    test::require(bot::bestMove(w, safety, targets).dir != 1,
                  "the explore target must not lure the dragon into a real dead end");
}

void crowdedOpenGroundSplits() {
    auto w = test::world(24, 24);
    test::body(w, {w.tileOf(12, 12), w.tileOf(11, 12), w.tileOf(10, 12), w.tileOf(9, 12)});
    test::dragon(w, 2, false, w.tileOf(12, 9), 3);
    test::dragon(w, 3, false, w.tileOf(15, 12), 3);
    test::dragon(w, 4, false, w.tileOf(12, 15), 3);
    test::require(wantsSplit(w), "teammate heads around open ground leave both halves room to split");
}

// A length-4 dragon at (12,12) heading east on open ground at round 60, with a head two steps from its head
// on each open side (north, east, south): teammates, or enemies.
bot::World hemmedIn(bool enemies) {
    auto w = test::world(24, 24);
    w.round = 60;
    test::body(w, {w.tileOf(12, 12), w.tileOf(11, 12), w.tileOf(10, 12), w.tileOf(9, 12)});
    test::dragon(w, 2, enemies, w.tileOf(12, 10), 3);
    test::dragon(w, 3, enemies, w.tileOf(14, 12), 3);
    test::dragon(w, 4, enemies, w.tileOf(12, 14), 3);
    return w;
}

// Every move's share against all other heads is under 6 tiles, but teammates take none of the split's
// territory (P.splitTerritoryEnemyOnly), so the dragon splits as the leaders do among teammates.
void teammatesDoNotBlockSplit() {
    auto w = hemmedIn(false);
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    auto const best = bot::bestMove(w, safety, targets);
    test::require(best.space < 6 && bot::splitTerritory(w, safety, best, 6) == 6 && best.room >= P.splitParentSpace,
                  "the teammates reach most of the parent's ground first, but it has physical room");
    test::require(bot::shouldSplit(w, controllerFor(w), safety, best) == P.childLength,
                  "teammate heads two steps away no longer hold back the split");
}

// The same ring of heads as enemies: they reach the parent's ground first, which refuses the split only while
// the territory gate is on; none is adjacent, so with it off (0) we split.
void enemyHeadsNearbyFollowTerritoryGate() {
    auto w = hemmedIn(true);
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    auto const best = bot::bestMove(w, safety, targets);
    test::require(bot::splitTerritory(w, safety, best, 6) < 6, "enemy heads two steps away take most of the parent's ground");
    bool const gateOpen = bot::splitTerritory(w, safety, best, P.splitParentTerritory) >= P.splitParentTerritory;
    test::require(bot::shouldSplit(w, controllerFor(w), safety, best) == (gateOpen ? P.childLength : 0),
                  "enemy heads that reach the parent's ground first refuse the split exactly when the gate says so");
}

// A 1-wide corridor east of our head (row 12, x 13..22) with one head at its far end: the split's territory
// counts corridor tiles we enter strictly before an enemy head, a tie going to the enemy; a teammate there
// takes none. Our depth at x is x - 12; an enemy at ex reaches x in ex - x steps.
void splitTerritoryCountsEnemyHeadsOnly() {
    auto territory = [](int headX, bool enemy) {
        auto w = test::world(24, 24);
        test::body(w, {w.tileOf(12, 12), w.tileOf(11, 12), w.tileOf(10, 12), w.tileOf(9, 12)});
        for (int x = 13; x <= 22; ++x) {
            test::wall(w, w.tileOf(x, 12), 0);
            test::wall(w, w.tileOf(x, 12), 2);
        }
        test::dragon(w, 2, enemy, w.tileOf(headX, 12), 3);
        bot::Safety safety;
        safety.prepare(w);
        bot::MoveEval east;
        east.dir = 1;
        east.dest = w.tileOf(13, 12);
        return bot::splitTerritory(w, safety, east, 6);
    };
    test::require(territory(20, true) == 3, "x 16 is a tie (4 steps each), so only x 13..15 are ours");
    test::require(territory(21, true) == 4, "one step further back, x 16 is ours (4 steps to 5) and x 17 the enemy's (5 to 4)");
    test::require(territory(20, false) == 6, "a teammate at the far end takes nothing");
}

// A dragon of `length` heading east at (12,12) beside a five-deep dead end, x 13..17 on row 12, whose
// tiles hold `pearls` pearls from the mouth inwards.
bot::World besideFarm(int length, int pearls, int width = 24, int height = 24) {
    auto w = test::world(width, height);
    std::vector<int> tiles;
    for (int i = 0; i < length; ++i)
        tiles.push_back(w.tileOf(12 - i, 12));
    test::body(w, tiles);
    for (int x = 13; x <= 17; ++x) {
        test::wall(w, w.tileOf(x, 12), 0);
        test::wall(w, w.tileOf(x, 12), 2);
    }
    test::wall(w, w.tileOf(17, 12), 1);
    for (int x = 13; x < 13 + pearls; ++x)
        test::pearl(w, w.tileOf(x, 12));
    return w;
}

int movesInto(bot::World const& w) {
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    targets.update(w);
    return bot::bestMove(w, safety, targets).dir;
}

void entersPearlFilledDeadEnd() {
    auto w = besideFarm(5, 5);
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    auto const east = bot::evaluateMove(w, safety, targets, 1);
    test::require(east.closed, "the corridor is still judged a closed pocket");
    test::require(movesInto(w) == 1, "five pearls repay the two segments left behind: go in and eat them");
    test::require(!wantsSplit(w), "entering a farm must not trigger the open-ground split");
}

void farmedDeadEndEndsWithSplitOut() {
    auto w = test::world(24, 24);
    test::body(w, {w.tileOf(17, 12), w.tileOf(16, 12), w.tileOf(15, 12), w.tileOf(14, 12), w.tileOf(13, 12)});
    for (int x = 13; x <= 17; ++x) {
        test::wall(w, w.tileOf(x, 12), 0);
        test::wall(w, w.tileOf(x, 12), 2);
    }
    test::wall(w, w.tileOf(17, 12), 1);
    test::require(wantsSplit(w) == 3, "at the dead end everything but two segments splits off and walks out");
    w.unitCount = 64;
    test::require(wantsSplit(w) == 0, "a full team cannot split out");
}

// A length-6 dragon filling a walled corridor from (12, 12) to its dead end at (17, 12), head at the dead end.
bot::World doomedInCorridor() {
    auto w = test::world(24, 24);
    std::vector<int> tiles;
    for (int x = 17; x >= 12; --x) {
        tiles.push_back(w.tileOf(x, 12));
        test::wall(w, w.tileOf(x, 12), 0);
        test::wall(w, w.tileOf(x, 12), 2);
    }
    test::body(w, tiles);
    test::wall(w, w.tileOf(17, 12), 1);
    return w;
}

void doomedDragonSplitsOutInMergePhase() {
    auto w = doomedInCorridor();
    w.round = P.mergeRound + 20;
    test::require(wantsSplit(w) == 4, "the merge phase stops ordinary splits, not a length-6 doomed dragon's split-out");
}

// The debug log line tells an ordinary split from a doomed dragon's split-out, and a refused split names none.
void splitNamesItsRule() {
    auto open = straightDragon();
    test::require(splitRule(open) == "split", "an ordinary split on open ground is named split");
    open.round = P.mergeRound;
    test::require(splitRule(open) == "none", "a split the merge round refuses names no rule");
    auto doomed = doomedInCorridor();
    test::require(splitRule(doomed) == "splitout", "a doomed dragon's split is named splitout");
    doomed.round = P.mergeRound + 20;
    test::require(splitRule(doomed) == "splitout", "a split-out in the merge phase is still named splitout");
}

void shortDragonSplitsOutWhenDoomed() {
    auto w = test::world(24, 24);
    test::body(w, {w.tileOf(17, 12), w.tileOf(16, 12), w.tileOf(15, 12)});
    for (int x = 15; x <= 17; ++x) {
        test::wall(w, w.tileOf(x, 12), 0);
        test::wall(w, w.tileOf(x, 12), 2);
    }
    test::wall(w, w.tileOf(17, 12), 1);
    test::require(wantsSplit(w) == 0, "three segments cannot leave a legal child behind a length-2 parent");
}

void farmNeedsEnoughPearlsToSplitOut() {
    test::require(movesInto(besideFarm(5, 0)) != 1, "a dead end without pearls or fast tiles is still refused");
    test::require(movesInto(besideFarm(5, 1)) != 1, "one pearl does not repay the parent left behind");
    test::require(movesInto(besideFarm(2, 2)) == 1, "length 2 plus two pearls ends long enough to split out");
    test::require(movesInto(besideFarm(3, 5)) == 1, "a length-3 dragon grows inside to a length that splits out");
}

void farmCountsFastTilesAsPearls() {
    auto w = besideFarm(4, 1);
    test::require(movesInto(w) != 1, "one visible pearl alone is not worth a dead end");
    for (int x = 15; x <= 17; ++x)
        test::fast(w, w.tileOf(x, 12));
    test::require(movesInto(w) == 1, "empty fast tiles refill before we reach them and count as pearls");
}

void farmIsHeldByOneDragon() {
    auto inside = besideFarm(5, 5);
    test::dragon(inside, 2, false, inside.tileOf(16, 12), 3);
    test::require(movesInto(inside) != 1, "a teammate already inside owns the farm");
    auto mouth = besideFarm(5, 5);
    test::dragon(mouth, 2, false, mouth.tileOf(12, 11), 3);
    test::require(movesInto(mouth) != 1, "a dragon at the mouth could seal us in");
    auto full = besideFarm(5, 5);
    full.unitCount = 64;
    test::require(movesInto(full) != 1, "at the unit cap there is no split out, so the pocket is a grave");
}

// MoveEval::farm, which the debug log line's farm flag reports, marks exactly a closed move farmEntryOk accepted.
void farmEntryIsFlagged() {
    auto const farm = besideFarm(4, P.farmMinPearls + 1);
    bot::Safety safety;
    safety.prepare(farm);
    bot::Targets targets;
    targets.update(farm);
    auto const entered = bot::bestMove(farm, safety, targets);
    test::require(entered.dir == 1 && entered.closed && entered.farm, "entering a pearl-filled pocket is a farm entry");
    auto full = besideFarm(4, P.farmMinPearls + 1);
    full.unitCount = full.unitLimit;
    safety.prepare(full);
    targets.update(full);
    auto const open = bot::bestMove(full, safety, targets);
    test::require(open.dir != 1 && !open.closed && !open.farm, "the same dragon taking an open move is no farm entry");
    auto const refused = bot::evaluateMove(full, safety, targets, 1);
    test::require(refused.closed && !refused.farm, "a closed move farmEntryOk refuses is no farm entry");
}

// A farm trip ends in a split-out, which would spend a rescue slot, so on a big map with P.farmHonorsReserve entry
// needs a slot outside the reserve: refused at the reserve, allowed one below it.
void farmLeavesRescueSlots() {
    auto w = besideFarm(5, 5, 64, 64);
    int const reserveFull = w.unitLimit - bot::rescueReserve(w);
    w.unitCount = reserveFull - 1;
    test::require(movesInto(w) == 1, "below the reserve the pearl-filled pocket is farmed");
    w.unitCount = reserveFull;
    test::require(P.farmHonorsReserve && bot::rescueReserve(w) > 0 && movesInto(w) != 1,
                  "at the reserve the pocket is refused, keeping the reserved slots for doomed dragons");
}

// A teammate head north of our length-4 dragon, beyond the repel radius, takes a share of the north move's ground.
// The pearl is diagonal (north-east), so north and east are equally far from it: space alone tells them apart.
bot::World pearlBetweenTwoShares() {
    auto w = test::world(10, 10);
    test::body(w, {w.tileOf(5, 5), w.tileOf(4, 5), w.tileOf(3, 5), w.tileOf(2, 5)});
    test::pearl(w, w.tileOf(6, 4));
    test::dragon(w, 2, false, w.tileOf(4, 2), 3);
    return w;
}

// The debug log line's alt, gap and term: what the chosen move beat, and the score part that differs most.
void decidingTermNamesSpace() {
    auto const w = pearlBetweenTwoShares();
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    targets.update(w);
    auto const moves = bot::evaluateMoves(w, safety, targets);
    auto const best = bot::bestOf(moves, w.facing[w.head]);
    auto const& alt = bot::runnerUp(moves, best.dir);
    test::require(best.dir == 1 && alt.dir == 0, "east beats north, and north is the runner-up");
    test::require(best.safe && alt.safe && best.terms[bot::TERM_TARGET] == alt.terms[bot::TERM_TARGET] &&
                      best.terms[bot::TERM_REPEL] == 0 && alt.terms[bot::TERM_REPEL] == 0,
                  "both moves are safe, equally far from the pearl, and out of the teammate's repel radius");
    test::require(alt.space < best.space && std::string(bot::decidingTerm(best, alt)) == "space",
                  "the teammate's share of the north ground decides it: term space");
    test::require(best.score - alt.score == P.spaceWeight * (best.space - alt.space), "the gap is the space term's");
}

void decidingTermNamesTarget() {
    auto w = straightDragon();
    test::pearl(w, w.tileOf(8, 4));
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    targets.update(w);
    auto const moves = bot::evaluateMoves(w, safety, targets);
    auto const best = bot::bestOf(moves, w.facing[w.head]);
    auto const& alt = bot::runnerUp(moves, best.dir);
    test::require(best.dir == 0 && alt.dir == 1, "north steps toward the pearl; east is the first of two steps away");
    test::require(std::string(bot::decidingTerm(best, alt)) == "target" &&
                      best.score - alt.score == 2 * P.targetWeight,
                  "two steps of pearl distance decide it: term target");
    test::require(bot::runnerUp(moves, alt.dir).dir == best.dir,
                  "a move chosen over the best-scored (a hunt) logs the best-scored as its runner-up");
    test::require(std::string(bot::decidingTerm(moves[1], moves[2])) == "none",
                  "east and south are alike in every part: no deciding term");
}

void decidingTermNamesPenalty() {
    auto w = straightDragon();
    test::wall(w, w.head, 0);
    test::wall(w, w.head, 2);
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    targets.update(w);
    auto const moves = bot::evaluateMoves(w, safety, targets);
    auto const best = bot::bestOf(moves, w.facing[w.head]);
    auto const& alt = bot::runnerUp(moves, best.dir);
    test::require(best.dir == 1 && std::string(bot::decidingTerm(best, alt)) == "penalty",
                  "with kelp north and south and our body west, east beats a lethal move: term penalty");
}

// P.roomByRoundsLeft (I496, I398): our 30-long dragon, head at (32,32) with the body trailing south, has a dead end
// of `pocket` tiles west (x 31 down), a wall east and an open step north beside a 2-long enemy head (`enemy`) or, with
// `pearl`, a pearl on the dead end's first tile. A survival fill asks at most for the tiles the game's remaining moves
// use (holdCap), so late in the game a dead end that holds the dragon to the end is open.
bot::World lastRounds(int round, int pocket, bool enemy, bool pearl) {
    constexpr int N = 0, E = 1, S = 2, W = 3;
    (void)S;
    auto w = test::world(64, 64);
    std::vector<int> tiles;
    for (int i = 0; i < 30; i++)
        tiles.push_back(w.tileOf(32, 32 + i));
    test::body(w, tiles);
    w.round = round;
    w.unitCount = 20;
    for (int x = 32 - pocket; x <= 31; x++) {
        test::wall(w, w.tileOf(x, 32), N);
        test::wall(w, w.tileOf(x, 32), S);
    }
    test::wall(w, w.tileOf(32 - pocket, 32), W);
    test::wall(w, w.head, E);
    if (enemy)
        test::dragon(w, 40, true, w.tileOf(33, 31), 2);
    if (pearl)
        test::pearl(w, w.tileOf(31, 32));
    return w;
}

struct LastRounds {
    int best;
    bool pocketClosed;
};

LastRounds lastRoundsMove(bot::World const& w) {
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    targets.update(w);
    return {bot::bestOf(bot::evaluateMoves(w, safety, targets)).dir, bot::evaluateMove(w, safety, targets, 3).closed};
}

void lastRoundsTakeAPocketThatHoldsToTheEnd() {
    constexpr int N = 0, W = 3;
    int constexpr last = unswbc::Constants::MAX_ROUNDS - 1;
    test::require(lastRoundsMove(lastRounds(300, 5, true, false)).best == N,
            "at r300 a 5-tile dead end is far too small: the open step, ram risk and all");
    if (!P.roomByRoundsLeft)
        return;
    test::require(lastRoundsMove(lastRounds(last - 2, 5, true, false)).best == W,
            "3 rounds from the end the 5-tile dead end holds the dragon: it beats the ram risk");
    test::require(!lastRoundsMove(lastRounds(last - 4, 5, true, false)).pocketClosed &&
                lastRoundsMove(lastRounds(last - 5, 5, true, false)).pocketClosed,
            "the dead end is open from the round its 5 tiles hold the landing and every move after it");
    test::require(lastRoundsMove(lastRounds(last, 1, false, true)).best == W,
            "on the final round a pearl in a one-tile nook is taken though the nook has no way on (I398)");
    test::require(lastRoundsMove(lastRounds(last - 1, 1, false, true)).best == N, "a round earlier the nook is refused");
}

} // namespace

int main() {
    return test::suite({
        {"the last rounds take a pocket that holds to the end", lastRoundsTakeAPocketThatHoldsToTheEnd},
        {"safe exit beats wall, body, and friendly head", avoidsWallBodyAndFriendlyHead},
        {"deciding term: space between equally far moves", decidingTermNamesSpace},
        {"deciding term: target distance, runner-up of a hunt, none", decidingTermNamesTarget},
        {"deciding term: penalty against a lethal move", decidingTermNamesPenalty},
        {"trapped dragon spares its teammate", trappedDragonHitsWallRatherThanTeammate},
        {"safe immediate pearl wins", takesPearlAmongSafeMoves},
        {"safe ties keep our heading", tiedSafeMovesKeepHeading},
        {"diagonal target keeps heading unless a move is better", diagonalTargetKeepsHeadingUnlessAMoveIsBetter},
        {"known exit beats unknown portal", avoidsUnknownPortalWhenSafeExitExists},
        {"closed pearl pocket is rejected", rejectsPearlInClosedPocket},
        {"pearl at a blind corridor mouth is eaten", eatsPearlAtBlindCorridorMouth},
        {"pearl in a corridor closed in view is refused", refusesPearlInCorridorClosedInView},
        {"blind corridor loses a tie to an open move", blindCorridorLosesTieToOpenMove},
        {"long dragon explores through blind corridor", longDragonFollowsExploreThroughBlindCorridor},
        {"enemy ram risk outweighs pearl", avoidsPearlAnEnemyCanRam},
        {"outnumbered: tile beside equal head avoided", outnumberedAvoidsTileBesideEqualHead},
        {"teammate near the drop: tile beside equal head kept", teammateNearDropKeepsTileBesideEqualHead},
        {"tied counts: tile beside equal head kept", tiedCountsKeepTileBesideEqualHead},
        {"outnumbered and surrounded still moves", outnumberedAndSurroundedStillMoves},
        {"teammate's only exit is left open", leavesTeammateItsOnlyExit},
        {"tile taken when teammate has another exit", takesTileWhenTeammateHasAnotherExit},
        {"safe moves spread from teammates", spreadsOutFromFriendlyHeads},
        {"favorable head-on trade is chosen", takesFavorableHeadOnTrade},
        {"last units avoid head-on trade", preservesLastUnitsInsteadOfAttacking},
        {"protected king avoids head-on trade", protectedKingDoesNotAttack},
        {"spawn target attracts only when ready", waitsBesideFutureSpawnUntilReady},
        {"safe move follows exploration target", followsExplorationTargetWithoutPearls},
        {"open board permits legal split", openBoardAllowsLegalSplit},
        {"endgame boundary stops splits", splitStopsAtEndgameBoundary},
        {"split needs two legal halves", splitNeedsEnoughSegmentsForBothHalves},
        {"split honors team unit cap", splitHonorsTeamUnitCap},
        {"ordinary split keeps rescue slots", ordinarySplitKeepsRescueSlots},
        {"doomed long dragon splits out into a rescue slot", doomedLongDragonSplitsOutIntoRescueSlot},
        {"rescue reserve only on big maps", rescueReserveOnlyOnBigMaps},
        {"split needs known tail", splitNeedsKnownTail},
        {"split avoids adjacent enemy", splitAvoidsAdjacentEnemyHead},
        {"closed parent pocket holds only longer splits", closedParentPocketHoldsOnlyLongerSplits},
        {"split in a ring corridor follows the parent space gate", splitInRingMeetsParentSpaceGate},
        {"split needs unoccupied child exit", splitRequiresAnUnoccupiedChildExit},
        {"split child exit must have been seen", splitChildExitMustHaveBeenSeen},
        {"split beside a small child pocket follows the child space gate", splitIntoSmallChildPocketFollowsChildGate},
        {"contested open ground still follows pearl", contestedOpenGroundStillFollowsPearl},
        {"dead-end corridor is still refused", deadEndCorridorIsStillRefused},
        {"crowded open ground splits", crowdedOpenGroundSplits},
        {"teammates do not block split", teammatesDoNotBlockSplit},
        {"enemy heads two steps away follow the territory gate", enemyHeadsNearbyFollowTerritoryGate},
        {"split territory counts enemy heads only", splitTerritoryCountsEnemyHeadsOnly},
        {"fold parts start together", foldPartsStartTogether},
        {"pearl-filled dead end is entered", entersPearlFilledDeadEnd},
        {"farm ends with a split out", farmedDeadEndEndsWithSplitOut},
        {"doomed dragon splits out in merge phase", doomedDragonSplitsOutInMergePhase},
        {"split names its rule", splitNamesItsRule},
        {"length 3 cannot split out", shortDragonSplitsOutWhenDoomed},
        {"farm needs pearls to split out", farmNeedsEnoughPearlsToSplitOut},
        {"fast tiles count as farm pearls", farmCountsFastTilesAsPearls},
        {"a farm entry is flagged", farmEntryIsFlagged},
        {"one dragon holds a farm", farmIsHeldByOneDragon},
        {"farm leaves rescue slots", farmLeavesRescueSlots},
    });
}
