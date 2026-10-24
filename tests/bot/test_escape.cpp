#include "support.hpp"
#include "escape.hpp"

namespace {

constexpr int N = 0, E = 1, W = 3;

bot::Escape escapeFor(bot::World const& w) {
    bot::Safety safety;
    safety.prepare(w);
    return bot::findEscape(w, safety);
}

// Our dragon of `length` in a 1-wide slot: head at (8,8), body trailing south, kelp east and west of the head,
// so the only step is north onto (8,7). An enemy head sits at (8,6), right beyond it. Kelp on the west side of
// (8,7) leaves one sprint, NE, whose end (9,7) that head reaches in 2 steps through (9,6).
bot::World slot(int length, int enemyLength) {
    auto w = test::world();
    std::vector<int> tiles;
    for (int i = 0; i < length; ++i)
        tiles.push_back(w.tileOf(8, (8 + i) % 16));
    test::body(w, tiles);
    test::wall(w, w.head, E);
    test::wall(w, w.head, W);
    test::wall(w, w.tileOf(8, 7), W);
    test::dragon(w, 2, true, w.tileOf(8, 6), enemyLength);
    return w;
}

// slot() with kelp on the north side of (9,7): with (8,7) our new neck, the head's shortest way to (9,7) is
// (9,6), (10,6), (10,7), (9,7): 4 steps, which a head of length 5 or more can sprint.
bot::World walledSlot(int length, int enemyLength) {
    auto w = slot(length, enemyLength);
    test::wall(w, w.tileOf(9, 7), N);
    return w;
}

// The idea's first scenario: a length-10 dragon coiled so only N and E are free, a length-3 enemy head on the NE
// diagonal next to both. NN and EE end 2 steps from it; NW ends 4 away (our new neck at (8,7) and our old head
// block the short ways round), so we sprint NW.
void sprintsOutOfDiagonalRam() {
    auto w = test::world();
    test::body(w, {w.tileOf(8, 8), w.tileOf(7, 8), w.tileOf(7, 9), w.tileOf(8, 9), w.tileOf(9, 9), w.tileOf(10, 9),
                   w.tileOf(11, 9), w.tileOf(12, 9), w.tileOf(13, 9), w.tileOf(14, 9)});
    test::dragon(w, 2, true, w.tileOf(9, 7), 3);
    test::require(escapeFor(w).steps == std::vector<int>{N, W}, "NW is the only end the length-3 head can't reach in 2");
}

// Boundary on the enemy's reach: (9,7) is 2 steps from the head. A length-2 head can't sprint and can't get
// there; a length-3 head sprints 2 steps and can, so there is no sprint and we play as before. Behind kelp it is
// 4 steps away: the full model reaches it at length 5, while the legacy capped model cannot.
void reachDependsOnEnemyLength() {
    test::require(escapeFor(slot(10, 2)).steps == std::vector<int>{N, E}, "a length-2 head reaches 1 step");
    test::require(escapeFor(slot(10, 3)).steps.empty(), "a length-3 head sprints 2 steps onto the only end");
    test::require(P.fullEnemyReach ? escapeFor(walledSlot(10, 5)).steps.empty()
                                  : escapeFor(walledSlot(10, 5)).steps == std::vector<int>{N, E},
                  "the full model reaches four steps round kelp; switch-off retains the old escape");
    test::require(bot::ramReach(2) == 1 && bot::ramReach(3) == 2 && bot::ramReach(20) == P.maxRamSteps,
                  "length 2 reaches 1, 3 reaches 2, longer heads P.maxRamSteps");
}

// A head whose visible chain runs on out of sight may be longer than we see: it gets a full sprint's reach.
void hiddenBodyGetsFullReach() {
    auto w = slot(10, 2);
    w.visStamp[w.tileOf(8, 5)] = 0;
    test::require(escapeFor(w).steps.empty(), "a length-2 sighting with a tile beside it out of view may sprint 2");
}

// The only end out of reach opens into a closed 4-tile pocket: failing the room test, it is no escape.
void closedPocketIsNoEscape() {
    auto w = walledSlot(10, 4);
    for (int x = 9; x <= 12; ++x) {
        test::wall(w, w.tileOf(x, 7), N);
        test::wall(w, w.tileOf(x, 7), 2);
    }
    test::wall(w, w.tileOf(12, 7), E);
    test::require(escapeFor(w).steps.empty(), "a sprint into a closed 4-tile pocket for a length-10 dragon");
}

// With a single step out of the head's reach we just step: no sprint.
void safeStepMeansNoSprint() {
    auto w = test::world();
    std::vector<int> tiles;
    for (int i = 0; i < 10; ++i)
        tiles.push_back(w.tileOf(8, (8 + i) % 16));
    test::body(w, tiles);
    test::dragon(w, 2, true, w.tileOf(9, 7), 3);
    test::require(escapeFor(w).steps.empty(), "W is out of the diagonal head's reach, so we step there");
}

// Only dragons at least P.escapeMinLength long sprint away (a length-2 head is a bad trade for both).
void shortDragonsDoNotSprint() {
    test::require(escapeFor(slot(P.escapeMinLength, 2)).steps == std::vector<int>{N, E},
                  "a dragon P.escapeMinLength long sprints away from a length-2 head");
    test::require(escapeFor(slot(P.escapeMinLength - 1, 2)).steps.empty(), "one segment shorter does not");
}

// A longer enemy head is a good trade, not a threat: we don't run from it.
void goodTradeIsNoThreat() {
    test::require(escapeFor(slot(4, 10)).steps.empty(), "stepping next to a longer enemy head is not risky");
}

// Between two ends out of reach, a pearl on the path wins over direction order.
void prefersPearlOnPath() {
    auto w = walledSlot(10, 4);
    w.setEdge(w.edgeOf(w.tileOf(8, 7), W), bot::EDGE_OPEN);
    test::wall(w, w.tileOf(7, 7), N);
    test::require(escapeFor(w).steps == std::vector<int>{N, E}, "both ends are 4 steps away: NE comes first");
    test::pearl(w, w.tileOf(7, 7));
    test::require(escapeFor(w).steps == std::vector<int>{N, W}, "the pearl at the NW end wins");
}

} // namespace

int main() {
    return test::suite({
        {"sprints out of a diagonal ram", sprintsOutOfDiagonalRam},
        {"reach depends on enemy length", reachDependsOnEnemyLength},
        {"hidden body gets full reach", hiddenBodyGetsFullReach},
        {"closed pocket is no escape", closedPocketIsNoEscape},
        {"safe step means no sprint", safeStepMeansNoSprint},
        {"short dragons do not sprint", shortDragonsDoNotSprint},
        {"good trade is no threat", goodTradeIsNoThreat},
        {"prefers pearl on path", prefersPearlOnPath},
    });
}
