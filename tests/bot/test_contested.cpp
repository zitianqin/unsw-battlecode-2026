#include "support.hpp"
#include "targets.hpp"

#include <string>

using test::require;

namespace {

// A dragon born at (5, 8) on an open 32x32 board mirrored in y (candidate 0): the enemy's matching dragon was born at
// (5, 23). A home pearl 4 steps north of us and a middle pearl the enemy reaches within P.contestedSlack of us.
bot::World board(int middleY) {
    auto w = test::world(32, 32);
    w.head = w.bornHead = w.tileOf(5, 8);
    w.facing[w.head] = 2;
    w.symmetry = 0;
    test::pearl(w, w.tileOf(5, 4));
    test::pearl(w, w.tileOf(5, middleY));
    return w;
}

std::string at(bot::World const& w, int t) {
    return t < 0 ? "none" : "(" + std::to_string(w.xOf(t)) + ", " + std::to_string(w.yOf(t)) + ")";
}

// The middle pearl (7 steps, the enemy's 8) counts P.contestedWorth times and beats the home pearl 4 steps away; with the symmetry
// unknown the enemy's start is unknown and the home pearl wins, and a dragon that believes it is king counts it once
// (P.contestedKingOnce), so it too takes the home pearl.
void contested_pearl_beats_nearer_home_pearl() {
    auto w = board(15);
    bot::Targets targets;
    targets.update(w);
    require(targets.pearl == w.tileOf(5, 15), "the contested pearl wins, got " + at(w, targets.pearl));
    targets.king = true;
    targets.update(w);
    require(!targets.contested.on, "the king counts contested food once");
    require(targets.pearl == w.tileOf(5, 4), "king: the nearer pearl, got " + at(w, targets.pearl));
    targets.king = false;
    w.symmetry = -1;
    targets.update(w);
    require(targets.pearl == w.tileOf(5, 4), "no symmetry: the nearer pearl, got " + at(w, targets.pearl));
}

// Counting twice halves the cost, no more: a contested pearl 9 steps off (4.5) still loses to a home one 4 away, and a
// dragon born on the mirror line (within P.contestedSlack of its own image) can't tell the sides apart: off for it.
void doubled_food_still_ranks_by_cost() {
    auto w = board(17);
    bot::Targets targets;
    targets.update(w);
    require(targets.contested.at(w.tileOf(5, 17)), "the enemy reaches (5, 17) first");
    require(targets.pearl == w.tileOf(5, 4), "9 / 2 steps lose to 4, got " + at(w, targets.pearl));
    auto m = board(15);
    m.bornHead = m.tileOf(5, 15);
    targets.update(m);
    require(!targets.contested.on, "born on contested ground: off");
    require(targets.pearl == m.tileOf(5, 4), "off: the nearer pearl, got " + at(m, targets.pearl));
}

} // namespace

int main() {
    return test::suite({
        {"contested_pearl_beats_nearer_home_pearl", contested_pearl_beats_nearer_home_pearl},
        {"doubled_food_still_ranks_by_cost", doubled_food_still_ranks_by_cost},
    });
}
