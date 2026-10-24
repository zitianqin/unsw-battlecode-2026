#include "support.hpp"
#include "hunt.hpp"
#include "ram.hpp"

namespace {

// Our length-`length` dragon with its head at (8,8), body trailing west.
bot::World dragonOfLength(int length) {
    auto w = test::world();
    std::vector<int> tiles;
    for (int i = 0; i < length; ++i)
        tiles.push_back(w.tileOf(8 - i, 8));
    test::body(w, tiles);
    return w;
}

int huntFor(bot::World const& w) {
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    bot::DistField field;
    return bot::huntDirection(w, safety, bot::evaluateMoves(w, safety, targets), field);
}

constexpr int N = 0, E = 1, S = 2;

void closesInOnLongerHead() {
    auto w = dragonOfLength(4);
    test::dragon(w, 2, true, w.tileOf(13, 8), 12);
    bot::Safety safety;
    safety.prepare(w);
    test::require(bot::findRam(w, safety).steps.empty(), "five steps away is out of sprint range");
    test::require(huntFor(w) == E, "a length-4 dragon steps east towards the length-12 head five free steps away");
}

void equalOrShorterIsNotChased() {
    auto equal = dragonOfLength(4);
    test::dragon(equal, 2, true, equal.tileOf(13, 8), 4);
    test::require(huntFor(equal) < 0, "an enemy of our length is not chased when no teammate of ours would eat its drop");
    auto shorter = dragonOfLength(4);
    test::dragon(shorter, 2, true, shorter.tileOf(13, 8), 3);
    test::require(huntFor(shorter) < 0, "a shorter enemy is not chased");
}

void outnumberedDoesNotHunt() {
    auto w = dragonOfLength(4);
    test::dragon(w, 2, true, w.tileOf(13, 8), 12);
    test::dragon(w, 3, true, w.tileOf(2, 2), 3);
    test::require(huntFor(w) < 0, "one of ours against two enemy heads in view: nothing is chased");
    test::dragon(w, 5, false, w.tileOf(2, 13), 3);
    test::require(huntFor(w) == E, "with a teammate in view the counts are even and the hunt goes on");
}

void riskyApproachKeepsNormalMove() {
    auto w = dragonOfLength(4);
    test::dragon(w, 2, true, w.tileOf(13, 8), 12);
    test::dragon(w, 3, true, w.tileOf(9, 9), 2); // could ram (9,8), and trading our 4 for its 2 is bad
    test::dragon(w, 5, false, w.tileOf(2, 13), 3);
    test::dragon(w, 6, false, w.tileOf(13, 13), 3);
    test::require(huntFor(w) < 0, "the only approaching move is risky, so the normal move is kept");
}

void closedApproachKeepsNormalMove() {
    auto w = dragonOfLength(4);
    test::dragon(w, 2, true, w.tileOf(13, 8), 12);
    // A kelp-walled corridor (9..12, 8) whose only far exit is the enemy head: entering it is a closed pocket.
    for (int x = 9; x <= 12; x++) {
        test::wall(w, w.tileOf(x, 8), N);
        test::wall(w, w.tileOf(x, 8), S);
    }
    test::require(huntFor(w) < 0, "the only approaching move enters a closed pocket, so the normal move is kept");
}

void longDragonDoesNotHunt() {
    auto w = dragonOfLength(8);
    test::dragon(w, 2, true, w.tileOf(13, 8), 12);
    test::require(huntFor(w) < 0, "a length-8 dragon keeps its length instead of hunting");
}

void onlyHuntsBetweenMinAndMaxDistance() {
    auto near = dragonOfLength(4);
    test::dragon(near, 2, true, near.tileOf(11, 8), 12);
    test::require(huntFor(near) < 0, "three steps away is findRam's job");
    auto far = dragonOfLength(4);
    test::dragon(far, 2, true, far.tileOf(15, 8), 12);
    test::require(huntFor(far) < 0, "seven steps away is beyond the hunt");
}

void shortDragonClosesToItsOwnReach() {
    auto two = dragonOfLength(2);
    test::dragon(two, 2, true, two.tileOf(11, 8), 12);
    test::require(huntFor(two) == E, "a length-2 dragon cannot sprint, so it keeps closing in from three steps");
    auto adjacent = dragonOfLength(2);
    test::dragon(adjacent, 2, true, adjacent.tileOf(9, 8), 12);
    test::require(huntFor(adjacent) < 0, "one step away is findRam's one-step ram");
    auto three = dragonOfLength(3);
    test::dragon(three, 2, true, three.tileOf(11, 8), 12);
    test::require(huntFor(three) == E, "a length-3 dragon sprints two steps, so three steps away it closes in");
    auto near = dragonOfLength(3);
    test::dragon(near, 2, true, near.tileOf(10, 8), 12);
    test::require(huntFor(near) < 0, "two steps away a length-3 dragon rams instead");
}

void bodiesDoNotShortenTheHunt() {
    // A column of enemy body at x = 10 (tail known, so the default distance field walks through it): the head
    // at (12,8) is 4 steps through the body but many free steps around it.
    auto w = dragonOfLength(4);
    test::dragon(w, 2, true, w.tileOf(12, 8), 12);
    for (int y = 1; y <= 15; y++) {
        int const t = w.tileOf(10, y);
        w.occId[t] = 3;
        w.occEnemy[t] = 1;
        w.occK[t] = y;
        w.touched.push_back(t);
    }
    test::require(huntFor(w) < 0, "a head behind a body wall is not within the hunt's free steps");
}

void prefersLongestTarget() {
    auto w = dragonOfLength(4);
    test::dragon(w, 2, true, w.tileOf(12, 8), 8);  // four steps east
    test::dragon(w, 3, true, w.tileOf(8, 2), 12);  // six steps north
    test::dragon(w, 5, false, w.tileOf(1, 13), 3);
    test::dragon(w, 6, false, w.tileOf(14, 13), 3);
    test::require(huntFor(w) == N, "the length-12 head six steps north beats the length-8 head four steps east");
}

void pearlWinsTies() {
    auto w = dragonOfLength(4);
    test::dragon(w, 2, true, w.tileOf(11, 10), 12); // three east and two south: E and S both shorten the path
    test::pearl(w, w.tileOf(8, 9));
    test::require(huntFor(w) == S, "between equally approaching moves, the one eating a pearl wins");
    auto east = dragonOfLength(4);
    test::dragon(east, 2, true, east.tileOf(11, 10), 12);
    test::pearl(east, east.tileOf(9, 8));
    test::require(huntFor(east) == E, "and it follows the pearl east too");
}

// A length-3 dragon at (8,8) with an enemy head of its visible length five free steps east, and three teammates
// (length 2) at the given tiles.
bot::World equalPreyWith(std::array<std::pair<int, int>, 3> const& mates) {
    auto w = dragonOfLength(3);
    test::dragon(w, 2, true, w.tileOf(13, 8), 3);
    int id = 5;
    for (auto [x, y] : mates)
        test::dragon(w, id++, false, w.tileOf(x, y), 2);
    return w;
}

// P.huntEqualPrey: an even trade is worth walking to when our other heads would eat its drop (ramWorthFor's
// dropHarvestEdgeFor, with our body beside the victim), the price findRam puts on the ram once in reach.
void closesInOnEqualHeadWhenWeEatTheDrop() {
    auto w = equalPreyWith({{{12, 11}, {10, 11}, {7, 11}}});
    bot::Safety safety;
    safety.prepare(w);
    test::require(bot::findRam(w, safety).steps.empty(), "five steps away is out of sprint range");
    test::require(huntFor(w) == E, "three teammates close to the drop: the length-3 dragon closes in on the length-3 head");
}

void equalHeadWhoseDropWeLoseIsNotChased() {
    // The same three teammates, but more than P.harvestReach steps from every drop tile: nobody of ours eats it.
    auto w = equalPreyWith({{{0, 0}, {1, 0}, {0, 1}}});
    test::require(huntFor(w) < 0, "three teammates in view but none near the drop: no hunt of an even head");
}

void equalHeadPricedWhereWeWouldRamIt() {
    // Teammates trail behind us and two enemy heads wait beside the victim (six steps east): priced from where we
    // stand, our drop lies among our teammates, but at the ram our body lies beside the victim, among theirs.
    auto w = dragonOfLength(3);
    test::dragon(w, 2, true, w.tileOf(14, 8), 3);
    test::dragon(w, 3, true, w.tileOf(15, 10), 2);
    test::dragon(w, 4, true, w.tileOf(15, 6), 2);
    test::dragon(w, 5, false, w.tileOf(5, 9), 2);
    test::dragon(w, 6, false, w.tileOf(5, 7), 2);
    test::dragon(w, 7, false, w.tileOf(4, 8), 2);
    bot::Safety safety;
    safety.prepare(w);
    test::require(bot::dropHarvestEdge(w, w.tileOf(14, 8), {}) >= P.evenTradeMinHarvest,
                  "from where we stand our teammates would eat the drop");
    test::require(huntFor(w) < 0, "beside the victim its friends eat the drop, so the even head is not chased");
}

void longerPreyBeforeEqual() {
    auto w = equalPreyWith({{{12, 11}, {10, 11}, {7, 11}}});
    test::dragon(w, 3, true, w.tileOf(8, 3), 8); // five steps north, longer than us
    test::require(huntFor(w) == N, "a longer head in range is chased before an even one at the same distance");
}

void protectedDragonDoesNotHuntEqual() {
    auto w = equalPreyWith({{{12, 11}, {10, 11}, {7, 11}}});
    bot::Safety safety;
    safety.prepare(w);
    safety.protectedDragon = true;
    bot::Targets targets;
    bot::DistField field;
    test::require(bot::huntDirection(w, safety, bot::evaluateMoves(w, safety, targets), field) < 0,
                  "a protected dragon refuses every trade, so it never walks to an even one");
}

} // namespace

int main() {
    return test::suite({
        {"closes in on a longer head", closesInOnLongerHead},
        {"equal or shorter enemies are not chased", equalOrShorterIsNotChased},
        {"outnumbered: no hunt", outnumberedDoesNotHunt},
        {"risky approach: normal move", riskyApproachKeepsNormalMove},
        {"closed approach: normal move", closedApproachKeepsNormalMove},
        {"long dragon does not hunt", longDragonDoesNotHunt},
        {"hunt range: beyond ram reach, up to the max", onlyHuntsBetweenMinAndMaxDistance},
        {"short dragons close in to their own reach", shortDragonClosesToItsOwnReach},
        {"bodies do not shorten the hunt", bodiesDoNotShortenTheHunt},
        {"longest target first", prefersLongestTarget},
        {"pearl wins ties", pearlWinsTies},
        {"closes in on an even head whose drop we eat", closesInOnEqualHeadWhenWeEatTheDrop},
        {"even head whose drop we lose: no hunt", equalHeadWhoseDropWeLoseIsNotChased},
        {"even head priced where we would ram it", equalHeadPricedWhereWeWouldRamIt},
        {"longer prey before even", longerPreyBeforeEqual},
        {"protected dragon: no even hunt", protectedDragonDoesNotHuntEqual},
    });
}
