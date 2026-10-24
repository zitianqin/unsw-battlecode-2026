#include "support.hpp"
#include "targets.hpp"

#include <string>

using test::require;

namespace {

// A starter early in the game on an open width x height board, born in round 0 where its head is, P.childLength long
// (a split starter's half), seeing only its 7x7 view.
// Unseen edges stay open (the bot assumes that too); the board's own edges are all known open to start with.
bot::World opening(int width, int height, int hx, int hy, int round = 5) {
    auto w = test::world(width, height);
    w.round = round;
    w.head = w.tileOf(hx, hy);
    w.bornHead = w.head;
    w.length = P.childLength;
    w.firstRound = 0;
    w.facing[w.head] = 1;
    std::fill(w.visStamp.begin(), w.visStamp.end(), 0);
    std::fill(w.lastSeen.begin(), w.lastSeen.end(), -1);
    for (int dy = -3; dy <= 3; dy++) {
        for (int dx = -3; dx <= 3; dx++) {
            int const t = w.tileOf((hx + dx + width) % width, (hy + dy + height) % height);
            w.visStamp[t] = w.stamp;
            w.lastSeen[t] = w.round;
        }
    }
    return w;
}

// Kelp across every crossing of column x's west side.
void wallColumn(bot::World& w, int x) {
    for (int y = 0; y < w.H; y++)
        test::wall(w, w.tileOf(x, y), 3);
}

std::string at(bot::World const& w, int t) {
    return t < 0 ? "none" : "(" + std::to_string(w.xOf(t)) + ", " + std::to_string(w.yOf(t)) + ")";
}

// Born near a corner of a 32x32 board, the middle lines are the far ones on both axes: the dragon walks to the middle
// instead of exploring the ground beside it.
void starter_races_to_the_far_lines() {
    auto w = opening(32, 32, 2, 2);
    bot::Targets targets;
    targets.update(w);
    require(targets.pearl < 0, "nothing to eat in view");
    require(targets.race == w.tileOf(15, 15) && targets.explore == targets.race,
            "the target is the middle, on our side of both lines, got " + at(w, targets.explore));
    require(targets.exploreField.at(w.head) == 26, "the field is rooted at the race target");
}

// On a board shifted by half a side the teams start near the middle and the far lines are the seams: a dragon born at
// (18, 18) races to the seams' crossing, not the map file's middle.
void shifted_start_races_to_the_seams() {
    auto w = opening(32, 32, 18, 18);
    bot::Targets targets;
    targets.update(w);
    require(targets.race == w.tileOf(31, 31), "the far lines are the seams, got " + at(w, targets.race));
}

// A seam we have seen walled (Devil's home strips back onto it) is skipped even though it is the far line: the dragon
// takes the middle line, the way round the halves really meet.
void walled_far_line_is_skipped() {
    auto w = opening(32, 32, 12, 12);
    bot::Targets targets;
    targets.update(w);
    require(w.xOf(targets.race) == 0 && w.yOf(targets.race) == 0, "unwalled, the seams are the far lines");
    wallColumn(w, 0);
    targets.update(w);
    require(targets.race == w.tileOf(15, 0), "the walled x seam gives way to the x middle, got " + at(w, targets.race));
}

// A line counts as walled only with P.raceWallSeen kelp crossings seen and no open one.
void one_open_crossing_keeps_a_line() {
    auto w = opening(32, 32, 12, 12);
    wallColumn(w, 0);
    w.setEdge(w.edgeOf(w.tileOf(0, 20), 3), bot::EDGE_OPEN);
    bot::Targets targets;
    targets.update(w);
    require(w.xOf(targets.race) == 0, "a seam with a known opening is still a way through");

    auto u = opening(32, 32, 12, 12);
    for (int y = 0; y < u.H; y++)
        u.setEdge(u.edgeOf(u.tileOf(0, y), 3), y < P.raceWallSeen ? bot::EDGE_KELP : bot::EDGE_UNKNOWN);
    targets.update(u);
    require(u.xOf(targets.race) == 15, "P.raceWallSeen kelp crossings and no open one wall the seam");
    u.setEdge(u.edgeOf(u.tileOf(0, 0), 3), bot::EDGE_UNKNOWN);
    targets.update(u);
    require(u.xOf(targets.race) == 0, "one kelp crossing fewer does not");
}

// A remembered pearl or a spawn tile away from the race target doesn't hold the dragon in its home area, one on the way
// does; a pearl in view beside our birth tile waits too (race.hpp raceDefers: it is on no contested ground, and the
// band, unseen, counts as held by as many of theirs as ours).
void race_keeps_only_pearls_on_the_way() {
    auto w = opening(32, 32, 5, 5);
    int const behind = w.tileOf(3, 5), remembered = w.tileOf(5, 1), onTheWay = w.tileOf(10, 10);
    for (int t : {remembered, onTheWay}) {
        w.lastSeen[t] = w.round - 1;
        w.pearlSeen[t] = w.round - 1;
    }
    bot::Targets targets;
    targets.update(w);
    require(targets.pearl == onTheWay, "a remembered pearl on the way is kept, one behind is dropped, got " +
                                           at(w, targets.pearl));
    w.pearlSeen[onTheWay] = -1;
    targets.update(w);
    require(targets.pearl < 0 && targets.explore == targets.race, "with only the pearl behind us left, we race");

    int const spawn = w.tileOf(5, 3);
    w.spawnKind[spawn] = bot::SPAWN_YES;
    w.spawnRound[spawn] = w.round + 1;
    targets.update(w);
    require(targets.pearl < 0, "a spawn tile in view away from the target is not a pearl in view");

    test::pearl(w, behind);
    targets.update(w);
    require(targets.pearl < 0, "a home pearl in view waits too, got " + at(w, targets.pearl));
}

// A Devil-like start: a 32x16 board mirrored across x only, a starter born at (3, 5) racing to (15, 5). The seam
// crossings in view are kelp and the rest unseen, so the race takes the seam for walled, and its twin, born at (28, 5),
// reaches our home ground only through the middle.
bot::World walledHalves() {
    auto w = opening(32, 16, 3, 5);
    w.symmetry = 1;
    for (int y = 0; y < w.H; y++)
        w.setEdge(w.edgeOf(w.tileOf(0, y), 3), y >= 2 && y <= 8 ? bot::EDGE_KELP : bot::EDGE_UNKNOWN);
    return w;
}

// A pearl in view in our home ground, off the way, is left for later while the band is unseen: the twin would have to
// cross the middle first, the seam's unseen crossings counting as kelp. A pearl on a shortest path to the race target
// is still taken.
void race_passes_up_home_pearls() {
    auto w = walledHalves();
    bot::Targets targets;
    targets.update(w);
    require(targets.race == w.tileOf(15, 5), "racing along our birth row to the middle, got " + at(w, targets.race));
    int const home = w.tileOf(0, 8), onTheWay = w.tileOf(6, 5);
    test::pearl(w, home);
    targets.update(w);
    require(targets.pearl < 0 && targets.explore == targets.race, "the home pearl waits, got " + at(w, targets.pearl));
    require(!targets.bandHeads.observed, "nothing of the band is seen or heard");
    w.length = P.splitAtLength - 1;
    targets.update(w);
    require(targets.pearl == home, "one pearl short of a split, it eats it, got " + at(w, targets.pearl));
    w.length = P.childLength;
    test::pearl(w, onTheWay);
    targets.update(w);
    require(targets.pearl == onTheWay, "a pearl on the way costs the race nothing, got " + at(w, targets.pearl));
}

// Kept during the race: a pearl on the middle line far along it from the race target (the twin is about as far from it
// as we are) and a home pearl an enemy head in view is as near as we are.
void race_keeps_contested_pearls() {
    auto w = walledHalves();
    int const line = w.tileOf(15, 12);
    w.lastSeen[line] = w.round - 1;
    w.pearlSeen[line] = w.round - 1;
    bot::Targets targets;
    targets.update(w);
    require(targets.pearl == line, "a pearl on the middle line is contested, got " + at(w, targets.pearl));

    auto u = walledHalves();
    int const home = u.tileOf(3, 2);
    test::pearl(u, home);
    test::dragon(u, 7, true, u.tileOf(6, 2), 2);
    targets = {};
    targets.update(u);
    require(targets.pearl == home, "an enemy head as near as we are contests it, got " + at(u, targets.pearl));
}

// The team belief's report on a block holding band tiles (the middle columns, which the twin reaches as soon as we do),
// out of our view and within P.enemyHalfLife rounds, reads the band when it saw a band tile: with no enemy head there
// the commit is released and the home pearl eaten; with one there it holds, until a teammate on the band outnumbers
// it. An older report says nothing, so the commit holds.
void race_commit_follows_band_heads() {
    auto w = walledHalves();
    w.round = P.raceRounds / 2;
    int const home = w.tileOf(0, 8);
    test::pearl(w, home);
    bot::Belief belief;
    belief.init(w);
    int const band = w.tileOf(16, 12), b = belief.blockOf(band);
    belief.enemyRound[b] = w.round - static_cast<int>(P.enemyHalfLife);
    belief.seen[w.tileOf(19, 15)] = belief.enemyRound[b];
    bot::Targets targets;
    targets.team = &belief;
    targets.update(w);
    require(targets.pearl < 0, "a report that saw only the block's cells off the band leaves the band unobserved, got " +
                                   at(w, targets.pearl));
    belief.seen[band] = belief.enemyRound[b];
    targets.update(w);
    require(targets.pearl == home, "a band heard empty releases the commit, got " + at(w, targets.pearl));
    belief.enemyThere[b] = 1;
    targets.update(w);
    require(targets.pearl < 0, "an enemy head heard there holds it, got " + at(w, targets.pearl));
    test::dragon(w, 3, false, w.tileOf(15, 9), 2);
    targets.update(w);
    require(targets.pearl == home, "a teammate there outnumbers it, got " + at(w, targets.pearl));
    w.dragons.pop_back();
    belief.enemyThere[b] = 0;
    belief.enemyRound[b] = belief.seen[band] = w.round - static_cast<int>(P.enemyHalfLife) - 1;
    targets.update(w);
    require(targets.pearl < 0, "a stale report leaves the band unobserved, got " + at(w, targets.pearl));
}

// The race ends once the target is in view and at P.raceRounds, and runs only for a dragon that reaches the target by
// P.raceRounds, on any map: judged by its path on the first turn it has a target, whatever it does later.
void race_boundaries() {
    auto w = opening(32, 32, 13, 13);
    w.bornHead = w.tileOf(2, 2);
    bot::Targets targets;
    targets.update(w);
    require(targets.race < 0, "the middle is in view: nothing left to race to");

    auto late = opening(32, 32, 2, 2, 0);
    targets = {};
    targets.update(late);
    late.round = P.raceRounds - 1;
    targets.update(late);
    require(targets.race >= 0, "the race runs until P.raceRounds");
    late.round = P.raceRounds;
    targets.update(late);
    require(targets.race < 0 && targets.explore != late.tileOf(15, 15),
            "from P.raceRounds the dragon explores as usual, the old race target dropped");

    // On a 64x64 map (4096 tiles) the middle is (31, 31): P.raceRounds steps from (11, 11), one more from (11, 10).
    require(P.raceRounds == 40, "precondition: the paths below are set for P.raceRounds 40");
    auto near = opening(64, 64, 11, 11, 0);
    targets = {};
    targets.update(near);
    require(targets.race == near.tileOf(31, 31), "a big map races when the target is P.raceRounds steps off");
    auto far = opening(64, 64, 11, 10, 0);
    targets = {};
    targets.update(far);
    require(targets.race < 0, "one step more and it doesn't");
    auto closer = opening(64, 64, 20, 20, 5);
    closer.bornHead = far.bornHead;
    targets.update(closer);
    require(targets.race < 0, "nor later on, once its first path said it would arrive too late");
}

// A dragon with no known birth tile (a test world, or before its first turn) never races, nor does a child born after
// round 0, whose birth tile may lie anywhere on its parent's way.
void only_dragons_born_in_round_0_race() {
    auto w = opening(32, 32, 2, 2);
    w.bornHead = -1;
    bot::Targets targets;
    targets.update(w);
    require(targets.race < 0, "no birth tile, no race");
    auto child = opening(32, 32, 2, 2);
    child.firstRound = 1;
    targets = {};
    targets.update(child);
    require(targets.race < 0, "a child born in round 1 doesn't race");
}

// Once the target has been in view the race is over, even after we walk on and lose sight of it.
void race_ends_for_good_once_the_target_was_seen() {
    auto w = opening(32, 32, 2, 2);
    bot::Targets targets;
    targets.update(w);
    int const target = targets.race;
    require(target == w.tileOf(15, 15), "racing to the middle");
    w.round = 20;
    w.lastSeen[target] = 15;
    targets.update(w);
    require(targets.race < 0 && targets.explore != target, "the middle was seen at round 15: explore as usual");
}

// With the map known to mirror only x (candidate 1), y can be shifted by any amount: the target keeps our birth row.
// While a candidate that flips y is still live, y aims at its far line too.
void an_axis_no_mirror_flips_keeps_our_birth_coordinate() {
    auto w = opening(32, 32, 2, 5);
    bot::Targets targets;
    targets.update(w);
    require(targets.race == w.tileOf(15, 15), "all three candidates live: both axes race");
    w.symLive = 1 << 1;
    targets.update(w);
    require(targets.race == w.tileOf(15, 5), "x mirrored only: the row stays ours, got " + at(w, targets.race));
    w.symLive = 7;
    w.symmetry = 0;
    targets.update(w);
    require(targets.race == w.tileOf(2, 15), "y mirrored only: the column stays ours, got " + at(w, targets.race));
}

} // namespace

int main() {
    return test::suite({
        {"starter races to the far lines", starter_races_to_the_far_lines},
        {"shifted start races to the seams", shifted_start_races_to_the_seams},
        {"walled far line is skipped", walled_far_line_is_skipped},
        {"one open crossing keeps a line", one_open_crossing_keeps_a_line},
        {"race keeps only pearls on the way", race_keeps_only_pearls_on_the_way},
        {"race passes up home pearls", race_passes_up_home_pearls},
        {"race commit follows band heads", race_commit_follows_band_heads},
        {"race keeps contested pearls", race_keeps_contested_pearls},
        {"race boundaries", race_boundaries},
        {"only dragons born in round 0 race", only_dragons_born_in_round_0_race},
        {"race ends for good once the target was seen", race_ends_for_good_once_the_target_was_seen},
        {"an axis no mirror flips keeps our birth coordinate", an_axis_no_mirror_flips_keeps_our_birth_coordinate},
    });
}
