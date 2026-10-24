#include "support.hpp"
#include "scoring.hpp"

// Swarm jams at the unit cap (jam.hpp): an open move whose way out another head can take before our next move is
// refused while another open move can't be cut off.

namespace {

// Our length-4 dragon at (8,8) facing east, with a pearl target east at (9,8). East leads into a corridor
// (9..12, 8), walled north and south, whose only way out is (13,8); the rest of the board is open.
bot::World corridorEast() {
    auto w = test::world();
    test::body(w, {w.tileOf(8, 8), w.tileOf(7, 8), w.tileOf(6, 8), w.tileOf(5, 8)});
    w.facing[w.head] = 1;
    for (int x = 9; x <= 12; x++) {
        test::wall(w, w.tileOf(x, 8), 0);
        test::wall(w, w.tileOf(x, 8), 2);
    }
    test::pearl(w, w.tileOf(9, 8));
    w.unitCount = w.unitLimit;
    return w;
}

bot::Targets pearlTarget(bot::World const& w) {
    bot::Targets targets;
    targets.pearl = w.tileOf(9, 8);
    targets.pearlField.build(w, targets.pearl, 20);
    return targets;
}

bot::MoveEval best(bot::World const& w) {
    bot::Safety safety;
    safety.prepare(w);
    return bot::bestMove(w, safety, pearlTarget(w));
}

void corridorMouthBesideHeadIsRefusedAtCap() {
    auto w = corridorEast();
    test::dragon(w, 2, false, w.tileOf(13, 7), 4); // can step onto the mouth (13,8) before our next move
    auto const move = best(w);
    test::require(move.dir != 1 && move.safe, "at the cap, an open move is taken over the corridor a teammate can seal");
    bot::Safety safety;
    safety.prepare(w);
    auto const east = bot::evaluateMove(w, safety, pearlTarget(w), 1);
    test::require(east.open && east.cutOff, "the corridor is physically open, but the head beside its mouth can cut it off");
}

void corridorWithFreeMouthIsTakenAtCap() {
    auto w = corridorEast();
    test::dragon(w, 2, false, w.tileOf(13, 5), 4); // two steps from the mouth: it can't take it before our next move
    test::require(best(w).dir == 1, "with no head beside the mouth, the pearl in the corridor is eaten");
}

// The idea's case: a U-shaped teammate whose tail at (13,8) is the corridor's way out (it frees on schedule, so the
// physical fill calls the corridor open), while that teammate's head at (13,7) sits beside it.
void uShapedTeammateTailMouthIsRefusedAtCap() {
    auto w = corridorEast();
    int const tiles[] = {w.tileOf(13, 7), w.tileOf(14, 7), w.tileOf(14, 8), w.tileOf(13, 8)};
    test::dragon(w, 2, false, tiles[0], 4);
    for (int k = 0; k < 4; k++) {
        w.occId[tiles[k]] = 2;
        w.occK[tiles[k]] = 3 - k;
        w.touched.push_back(tiles[k]);
    }
    bot::Safety safety;
    safety.prepare(w);
    auto const east = bot::evaluateMove(w, safety, pearlTarget(w), 1);
    test::require(east.open && !east.closed, "the teammate's tail frees in time: physically the corridor is open");
    test::require(best(w).dir != 1, "its head beside the tail can seal the corridor first: not entered at the cap");
}

void belowCapMarginCorridorIsTaken() {
    auto w = corridorEast();
    test::dragon(w, 2, false, w.tileOf(13, 7), 4);
    w.unitCount = w.unitLimit - P.headReachCapMargin;
    test::require(best(w).dir != 1, "at the cap margin the corridor is refused");
    w.unitCount = w.unitLimit - P.headReachCapMargin - 1;
    test::require(best(w).dir == 1, "below the cap margin a trapped dragon can split out: the pearl is eaten as before");
}

// When every open move can be cut off, the check can't tell them apart and the scores stand.
void everyMoveCutOffKeepsScores() {
    auto w = corridorEast();
    test::dragon(w, 2, false, w.tileOf(13, 7), 4);
    test::wall(w, w.head, 0);
    test::wall(w, w.head, 2);
    auto const move = best(w);
    test::require(move.dir == 1 && move.cutOff && move.safe,
                  "the only open move can be cut off, so it is played unpenalised");
}

// Open ground crowded with teammates (the E0027 contested-ground case) is still open.
void crowdedOpenGroundStaysOpen() {
    auto w = test::world();
    test::body(w, {w.tileOf(8, 8), w.tileOf(7, 8), w.tileOf(6, 8), w.tileOf(5, 8)});
    w.unitCount = w.unitLimit;
    test::pearl(w, w.tileOf(9, 8));
    int id = 2;
    for (auto [x, y] : {std::pair{10, 6}, {10, 10}, {11, 8}, {6, 6}, {6, 10}, {8, 5}, {8, 11}})
        test::dragon(w, id++, false, w.tileOf(x, y), 3);
    bot::Safety safety;
    safety.prepare(w);
    auto const moves = bot::evaluateMoves(w, safety, pearlTarget(w));
    for (int dir : {0, 1, 2})
        test::require(moves[dir].open && !moves[dir].cutOff, "open ground among teammates' heads can't be cut off");
    test::require(best(w).dir == 1, "the pearl among teammates is eaten");
}

} // namespace

int main() {
    return test::suite({
        {"corridor mouth beside a head is refused at the cap", corridorMouthBesideHeadIsRefusedAtCap},
        {"corridor with a free mouth is taken at the cap", corridorWithFreeMouthIsTakenAtCap},
        {"U-shaped teammate's tail mouth is refused at the cap", uShapedTeammateTailMouthIsRefusedAtCap},
        {"corridor refused from the cap margin, taken below it", belowCapMarginCorridorIsTaken},
        {"every open move cut off keeps scores", everyMoveCutOffKeepsScores},
        {"crowded open ground stays open", crowdedOpenGroundStaysOpen},
    });
}
