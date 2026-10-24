#include "support.hpp"
#include "targets.hpp"

#include <cmath>

using test::require;

namespace {

void visible_pearl_selects_shortest_reachable_target() {
    auto w = test::world();
    int const near = w.tileOf(10, 8), far = w.tileOf(8, 12);
    test::pearl(w, near);
    test::pearl(w, far);
    bot::Targets targets;
    targets.update(w);
    require(targets.pearl == near && targets.pearlReady == 0, "a visible pearl two steps away must beat one four steps away");
    require(targets.pearlField.at(near) == 0 && targets.pearlField.at(w.head) == 2,
            "the retained field must be rooted at the chosen pearl");
}

void remembered_pearl_is_usable_until_it_becomes_stale() {
    auto w = test::world();
    int const pearl = w.tileOf(10, 8);
    w.visStamp[pearl] = 0;
    w.lastSeen[pearl] = 99;
    w.pearlSeen[pearl] = 99;
    bot::Targets targets;
    targets.choosePearl(w);
    require(targets.pearl == pearl, "a pearl seen last turn remains a target outside current vision");
    w.round = 1000;
    targets.choosePearl(w);
    require(targets.pearl == -1, "an ancient pearl sighting must not remain a target indefinitely");
}

void later_empty_observation_invalidates_remembered_pearl() {
    auto w = test::world();
    int const pearl = w.tileOf(10, 8);
    w.round = 101;
    w.pearlSeen[pearl] = 99;
    w.lastSeen[pearl] = 100;
    w.visStamp[pearl] = 0;
    bot::Targets targets;
    targets.choosePearl(w);
    require(targets.pearl == -1, "a later empty observation must invalidate an earlier pearl sighting");
    w.lastSeen[pearl] = 99;
    w.visStamp[pearl] = w.stamp;
    targets.choosePearl(w);
    require(targets.pearl == -1, "current empty vision must override remembered pearl presence");
}

void visible_pearl_beats_equally_close_unseen_pearl() {
    auto w = test::world();
    int const remembered = w.tileOf(8, 7), visible = w.tileOf(8, 9);
    test::pearl(w, visible);
    w.visStamp[remembered] = 0;
    w.lastSeen[remembered] = 99;
    w.pearlSeen[remembered] = 99;
    bot::Targets targets;
    targets.choosePearl(w);
    require(targets.pearl == visible, "confirmed food must beat equally distant remembered food despite its higher tile id");
}

void countdown_target_uses_first_move_after_spawn() {
    auto w = test::world();
    int const spawn = w.tileOf(11, 8);
    w.spawnKind[spawn] = bot::SPAWN_YES;
    w.spawnRound[spawn] = 101;
    bot::Targets targets;
    targets.choosePearl(w);
    require(targets.pearl == spawn, "a spawn due before our three-step arrival is worth approaching");
    require(targets.pearlReady == 2, "a spawn next round is available on move two, after this round's move");
    require(targets.pearlField.at(w.head) == 3, "countdown readiness must not alter travel distance");

    w.spawnRound[spawn] = 1000;
    targets.choosePearl(w);
    require(targets.pearl == -1, "a distant future spawn must not make us camp indefinitely");
}

// P.rateSpawnSlack: a spawn three steps away is worth waiting for while it fires no more than this dragon's steps per
// pearl found by exploring after we could arrive (the prior P.reachPriorSteps with no episodes of its own), and one a
// step later is not; a dragon whose exploring finds a pearl every step waits less than P.spawnSlack.
void spawn_wait_is_worth_what_exploring_costs_this_dragon() {
    auto w = test::world();
    int const spawn = w.tileOf(11, 8);
    w.spawnKind[spawn] = bot::SPAWN_YES;
    int const lastReady = 3 + static_cast<int>(P.reachPriorSteps); // ready is fires - round + 1
    w.spawnRound[spawn] = w.round + lastReady - 1;
    bot::Targets fresh;
    fresh.choosePearl(w);
    require(!P.rateSpawnSlack || (fresh.pearl == spawn && fresh.pearlReady == lastReady),
            "a new dragon waits for a spawn firing up to P.reachPriorSteps after it could arrive");
    w.spawnRound[spawn]++;
    fresh.choosePearl(w);
    require(fresh.pearl == -1, "a spawn a step past that is left to exploring");
    w.spawnRound[spawn] = w.round + 3 + P.spawnSlack - 1;
    bot::Targets quick;
    quick.exploreRate.episodes = quick.exploreRate.steps = 100;
    quick.choosePearl(w);
    require(quick.pearl == (P.rateSpawnSlack ? -1 : spawn),
            "a dragon that finds a pearl every step exploring leaves a spawn P.spawnSlack after its arrival");
}

void elapsed_countdown_requires_no_subsequent_empty_sighting() {
    auto w = test::world();
    int const spawn = w.tileOf(11, 8);
    w.visStamp[spawn] = 0;
    w.spawnKind[spawn] = bot::SPAWN_YES;
    w.spawnRound[spawn] = 99;
    w.lastSeen[spawn] = 98;
    bot::Targets targets;
    targets.choosePearl(w);
    require(targets.pearl == spawn && targets.pearlReady == 0,
            "an unseen countdown that has fired can supply an inferred pearl target");
    w.lastSeen[spawn] = 99;
    targets.choosePearl(w);
    require(targets.pearl == -1, "seeing an empty tile at its spawn round disproves the inferred pearl");
    w.lastSeen[spawn] = 98;
    w.spawnKind[spawn] = bot::SPAWN_NEVER;
    targets.choosePearl(w);
    require(targets.pearl == -1, "a never-spawns tile must ignore an obsolete stored countdown");
}

void equal_arrival_teammates_use_id_to_assign_one_owner() {
    auto w = test::world();
    w.myId = 2;
    w.head = w.tileOf(4, 4);
    int const pearl = w.tileOf(5, 4);
    test::pearl(w, pearl);
    test::dragon(w, 1, false, w.tileOf(6, 4), 4);
    bot::Targets targets;
    targets.choosePearl(w);
    require(targets.pearl == -1, "a lower-id teammate wins an equal arrival");

    w.dragons[0].id = 3;
    w.occId[w.tileOf(6, 4)] = 3;
    targets.choosePearl(w);
    require(targets.pearl == pearl, "we win the same arrival tie against a higher-id teammate");
}

void closer_teammate_owns_pearl_regardless_of_id() {
    auto w = test::world();
    w.head = w.tileOf(4, 4);
    int const pearl = w.tileOf(7, 4);
    test::pearl(w, pearl);
    test::dragon(w, 9, false, w.tileOf(8, 4), 2);
    bot::Targets targets;
    targets.choosePearl(w);
    require(targets.pearl == -1, "a teammate one step away beats our three-step arrival even with a higher id");
}

void enemy_ownership_requires_a_strictly_earlier_arrival() {
    auto w = test::world();
    w.myId = 2;
    w.head = w.tileOf(4, 4);
    int const pearl = w.tileOf(7, 4);
    test::pearl(w, pearl);
    test::dragon(w, 1, true, w.tileOf(8, 4), 2);
    bot::Targets targets;
    targets.choosePearl(w);
    require(targets.pearl == -1, "a closer visible enemy makes a pearl unavailable");

    w.occId[w.tileOf(8, 4)] = -1;
    w.dragons.clear();
    test::dragon(w, 1, true, w.tileOf(10, 4), 2);
    targets.choosePearl(w);
    require(targets.pearl == pearl, "an enemy arriving at the same time must not get teammate-id priority");
}

void countdown_wait_can_turn_distance_advantage_into_id_tie() {
    auto w = test::world();
    w.myId = 2;
    w.head = w.tileOf(4, 4);
    int const spawn = w.tileOf(5, 4);
    w.spawnKind[spawn] = bot::SPAWN_YES;
    w.spawnRound[spawn] = 102;
    test::dragon(w, 1, false, w.tileOf(7, 4), 3);
    bot::Targets targets;
    targets.choosePearl(w);
    require(targets.pearl == -1, "both dragons wait for move-three food, so the farther lower-id teammate owns it");
    w.dragons[0].id = 3;
    w.occId[w.tileOf(7, 4)] = 3;
    targets.choosePearl(w);
    require(targets.pearl == spawn, "the nearer dragon wins when it also wins the spawn-time id tie");
}

void teammate_claimed_nearest_pearl_leaves_another_choice() {
    auto w = test::world();
    w.head = w.tileOf(4, 4);
    int const claimed = w.tileOf(6, 4), available = w.tileOf(4, 7);
    test::pearl(w, claimed);
    test::pearl(w, available);
    test::dragon(w, 3, false, w.tileOf(7, 4), 4);
    bot::Targets targets;
    targets.choosePearl(w);
    require(targets.pearl == available, "losing the nearest pearl must continue the search for another owned pearl");
    require(targets.pearlField.at(available) == 0 && targets.pearlField.at(w.head) == 3,
            "the final field must belong to the selected alternative");
}

void nearest_teammates_keep_separate_pearls_in_e0022_counterexample() {
    auto shortDragon = test::world();
    shortDragon.round = 450;
    shortDragon.myId = 2;
    shortDragon.head = shortDragon.tileOf(3, 1);
    shortDragon.length = 3;
    int const shortPearl = shortDragon.tileOf(3, 0), longPearl = shortDragon.tileOf(0, 0);
    test::pearl(shortDragon, shortPearl);
    test::pearl(shortDragon, longPearl);
    test::dragon(shortDragon, 1, false, shortDragon.tileOf(1, 0), 8);
    bot::Targets shortTargets;
    shortTargets.choosePearl(shortDragon);
    require(shortTargets.pearl == shortPearl, "the short dragon must keep the pearl one move north of it");

    auto longDragon = test::world();
    longDragon.round = 450;
    longDragon.head = longDragon.tileOf(1, 0);
    longDragon.length = 8;
    test::pearl(longDragon, shortPearl);
    test::pearl(longDragon, longPearl);
    test::dragon(longDragon, 2, false, longDragon.tileOf(3, 1), 3);
    bot::Targets longTargets;
    longTargets.choosePearl(longDragon);
    require(longTargets.pearl == longPearl, "the long dragon must take its own one-step pearl rather than reserve both");
}

void unreachable_nearest_pearl_does_not_hide_reachable_food() {
    auto w = test::world();
    int const boxed = w.tileOf(9, 8), available = w.tileOf(8, 10);
    test::pearl(w, boxed);
    test::pearl(w, available);
    for (int direction = 0; direction < 4; ++direction)
        test::wall(w, boxed, direction);
    bot::Targets targets;
    targets.choosePearl(w);
    require(targets.pearl == available, "an enclosed nearby pearl must not beat reachable food");
}

// Equally cheap pearls go to the one along our facing, whichever comes first in tile order.
void equally_good_pearls_go_along_our_heading() {
    auto w = test::world();
    int const north = w.tileOf(8, 7), west = w.tileOf(7, 8);
    test::pearl(w, west);
    test::pearl(w, north);
    bot::Targets targets;
    w.facing[w.head] = 0;
    targets.choosePearl(w);
    require(targets.pearl == north, "facing north we take the pearl ahead");
    w.facing[w.head] = 3;
    targets.choosePearl(w);
    require(targets.pearl == west, "facing west we take the pearl ahead, not the lower tile");
}

// Spawns firing as we arrive: equal eating times go along our heading, a strictly sooner one off it still wins.
void equal_spawns_go_along_our_heading_but_a_sooner_one_wins() {
    auto w = test::world();
    w.facing[w.head] = 2;
    int const north = w.tileOf(8, 5), east = w.tileOf(11, 8), south = w.tileOf(8, 11);
    for (int t : {north, east, south}) {
        w.spawnKind[t] = bot::SPAWN_YES;
        w.spawnRound[t] = w.round + 1;
    }
    bot::Targets targets;
    targets.choosePearl(w);
    require(targets.pearl == south, "three spawns we eat on the same step: the one straight ahead");

    int const nearEast = w.tileOf(10, 8);
    w.spawnKind[nearEast] = bot::SPAWN_YES;
    w.spawnRound[nearEast] = w.round + 1;
    targets.choosePearl(w);
    require(targets.pearl == nearEast, "a spawn we eat a step sooner beats the one ahead");
}

void headless_visible_body_does_not_invent_a_pearl_owner() {
    auto w = test::world();
    w.myId = 3;
    w.head = w.tileOf(4, 4);
    int const pearl = w.tileOf(5, 4), body = w.tileOf(6, 4);
    test::pearl(w, pearl);
    w.occId[body] = 2;
    w.occK[body] = -1;
    w.touched.push_back(body);
    bot::Targets targets;
    targets.choosePearl(w);
    require(targets.pearl == pearl, "a body without a visible head must not be treated as a competing head");
    test::dragon(w, 2, false, body, 1);
    targets.choosePearl(w);
    require(targets.pearl == -1, "when that head becomes visible its lower-id arrival tie must count");
}

void vanished_pearl_resets_selection_and_exploration_retargets_when_seen() {
    auto w = test::world();
    int const pearl = w.tileOf(9, 8);
    test::pearl(w, pearl);
    bot::Targets targets;
    targets.update(w);
    require(targets.pearl == pearl, "the first turn must have a pearl target");
    w.pearlNow[pearl] = 0;
    std::fill(w.visStamp.begin(), w.visStamp.end(), 0);
    w.visStamp[w.head] = w.stamp;
    w.visStamp[pearl] = w.stamp;
    targets.update(w);
    require(targets.pearl == -1, "a now-empty observed pearl must clear the prior selection");
    int const first = targets.explore;
    require(first >= 0 && !w.visible(first), "without food, exploration must choose an unseen tile");
    require(targets.exploreField.at(first) == 0, "exploration must retain a field rooted at its destination");

    ++w.round;
    targets.update(w);
    require(targets.explore == first, "an unreached recent exploration destination must remain stable");
    w.visStamp[first] = w.stamp;
    ++w.round;
    targets.update(w);
    require(targets.explore >= 0 && targets.explore != first && !w.visible(targets.explore),
            "seeing an exploration destination must trigger a new unseen destination");
}

void fully_seen_empty_world_has_no_target() {
    auto w = test::world();
    bot::Targets targets;
    targets.update(w);
    require(targets.pearl == -1 && targets.explore == -1, "an empty fully observed board has no food or unexplored tile");
}

void fast_tile_cluster_outranks_a_nearer_lone_pearl() {
    auto w = test::world();
    int const lone = w.tileOf(8, 5);
    test::pearl(w, lone);
    for (int y = 12; y <= 15; ++y)
        test::pearl(w, w.tileOf(8, y));
    bot::Targets targets;
    targets.choosePearl(w);
    require(targets.pearl == lone, "among ordinary pearls, three steps beat the column four steps away");
    for (int y = 12; y <= 15; ++y)
        test::fast(w, w.tileOf(8, y));
    targets.choosePearl(w);
    require(targets.pearl != lone && w.fastTile(targets.pearl),
            "a column of fast tiles yields more per step than the nearer lone pearl");
}

void out_of_sight_fast_tile_stays_a_target() {
    auto w = test::world();
    int const farm = w.tileOf(8, 13);
    test::fast(w, farm);
    w.visStamp[farm] = 0;
    w.lastSeen[farm] = 10;
    w.spawnRound[farm] = 11;
    bot::Targets targets;
    targets.choosePearl(w);
    require(targets.pearl == farm, "a fast tile refills whenever empty, however long ago we saw it");
    w.resetCount[farm] = 0;
    targets.choosePearl(w);
    require(targets.pearl == -1, "an ordinary countdown seen 90 rounds ago is too stale to target");
}

// Kelp under row 8 except at x = gap: the tiles two south of the head are 2 * P.maxTargetDist - 4 to - 2 steps away
// by path, within twice the target range but not once, and no way round the torus is shorter.
void farm_behind_a_long_detour_is_worth_the_trip() {
    int const D = P.maxTargetDist;
    int const gap = D + 6;                 // the detour to (10, 10) is 2 * gap - 16 = 2 * D - 4 steps
    auto w = test::world(gap + D + 5, 2 * D + 10);
    for (int x = 0; x < w.W; ++x)
        if (x != gap)
            test::wall(w, w.tileOf(x, 8), 2);
    w.head = w.tileOf(8, 8);
    for (int x = 8; x <= 10; ++x)
        test::pearl(w, w.tileOf(x, 10));
    bot::Targets targets;
    targets.choosePearl(w);
    require(targets.pearl == -1, "ordinary pearls past P.maxTargetDist by path are beyond the target range");
    test::fast(w, w.tileOf(8, 10));
    targets.choosePearl(w);
    require(targets.pearl == -1, "a lone fast tile yields no more than a pearl, so it gets the same range");
    test::fast(w, w.tileOf(9, 10));
    test::fast(w, w.tileOf(10, 10));
    targets.choosePearl(w);
    require(targets.pearl == w.tileOf(10, 10) && targets.pearlField.at(w.head) == 2 * gap - 16,
            "three fast tiles together are worth twice the range: take the nearest of them");
}

// P.rateReach, turn by turn through update(): an explore episode counts every turn from the first with no pearl target
// to the next pearl eaten, the walk to what exploring found included; eating on a targeted walk starts none. With no
// episodes of its own a dragon keeps P.maxTargetDist, even on a board whose width plus height is shorter.
void explore_episodes_count_the_walk_to_what_they_found() {
    auto w = test::world();
    bot::Targets targets;
    auto const& rate = targets.exploreRate;
    require(w.W + w.H < P.maxTargetDist && targets.reachSteps(true) == P.maxTargetDist,
            "a dragon with no explore episodes of its own keeps P.maxTargetDist");
    int const found = w.tileOf(10, 8), next = w.tileOf(8, 12), last = w.tileOf(4, 8);
    targets.update(w);
    targets.update(w);
    require(targets.pearl == -1, "with no pearl anywhere the dragon explores");
    test::pearl(w, found);
    test::pearl(w, next);
    targets.update(w);
    targets.update(w);
    require(targets.pearl == found && rate.episodes == 0, "exploring found a pearl, not yet eaten");
    w.pearlNow[found] = 0;
    targets.update(w, true);
    require(rate.episodes == 1 && rate.steps == 4 && rate.open < 0,
            "two explore turns and a two-turn walk to the pearl they found are one four-step episode");
    require(targets.pearl == next, "the next pearl is in view");
    targets.update(w);
    w.pearlNow[next] = 0;
    test::pearl(w, last);
    targets.update(w, true);
    require(rate.episodes == 1 && rate.open < 0, "a pearl eaten on a targeted walk is no explore episode");
}

// A pearl 10 steps past P.maxTargetDist in open ground: a dragon whose explore episodes each took three times the
// mean P.reachPriorSteps walks there; one that finds a pearl every step exploring turns down one at half
// P.maxTargetDist, which the constant reach takes.
void pearl_reach_follows_what_exploring_costs_this_dragon() {
    int const D = P.maxTargetDist;
    auto w = test::world(2 * D + 40, 16);
    int const hx = w.xOf(w.head), hy = w.yOf(w.head);
    int const far = w.tileOf(hx + D + 10, hy), half = w.tileOf(hx - D / 2, hy);
    test::pearl(w, far);
    bot::Targets slow;
    slow.exploreRate.episodes = 4;
    slow.exploreRate.steps = static_cast<int>(3 * 4 * P.reachPriorSteps);
    slow.reach = slow.reachSteps(true);
    slow.choosePearl(w);
    require(slow.pearl == far, "a dragon exploring slowly walks to a pearl past P.maxTargetDist");

    test::pearl(w, half);
    w.pearlNow[far] = 0;
    bot::Targets quick;
    quick.exploreRate.episodes = quick.exploreRate.steps = 20;
    quick.choosePearl(w);
    require(quick.pearl == half, "the constant reach takes a pearl half P.maxTargetDist away");
    quick.reach = quick.reachSteps(true);
    quick.choosePearl(w);
    require(quick.pearl == -1, "a dragon that finds a pearl every step exploring explores instead");
}

// Tile t as watched resetting `resets` times to a mean countdown of `mean`, as World::noteCountdown records it.
void watchedResets(bot::World& w, int t, int resets, int mean) {
    w.resetCount[t] = resets;
    w.resetSum[t] = resets * mean;
    if (resets >= P.fastMinResets)
        w.resetTiles.push_back(t);
    w.spawnKind[t] = bot::SPAWN_YES;
}

// An open board 2 * P.maxTargetDist + 40 wide with our length-3 head at (8, 8): a pearl at (8 + P.maxTargetDist + 4, 8)
// is P.maxTargetDist + 4 steps away by path, beyond the target range, and the four beside it
// (one below, three two rows below) are 1-3 steps further. The five sit within P.farmClusterRadius of each other.
bot::World medium_patch_board(std::vector<int>& patch) {
    auto w = test::world(2 * P.maxTargetDist + 40, 16);
    w.head = w.tileOf(8, 8);
    w.length = 3;
    w.unitCount = 3;
    int const x = 8 + P.maxTargetDist + 4;
    patch = {w.tileOf(x, 8), w.tileOf(x, 9), w.tileOf(x - 1, 10), w.tileOf(x + 1, 10), w.tileOf(x, 10)};
    for (int t : patch)
        test::pearl(w, t);
    return w;
}

// I098: with P.rateYield, tiles slower than a fast tile still add to a farm's yield, weighed by their pearl rate.
void a_patch_of_medium_rate_tiles_is_worth_a_long_trip() {
    std::vector<int> patch;
    auto w = medium_patch_board(patch);
    bot::Targets targets;
    targets.choosePearl(w);
    require(targets.pearl == -1, "ordinary pearls past P.maxTargetDist are beyond the target range");
    for (int t : patch)
        watchedResets(w, t, 3, 12);
    require(P.rateYield || bot::Targets::yieldAt(w, patch[0]) == 1, "off, only fast tiles make a farm");
    targets.choosePearl(w);
    require(!P.rateYield || (targets.pearl == patch[0] && targets.pearlField.at(w.head) == P.maxTargetDist + 4),
            "five tiles resetting every 12 rounds or so are a farm worth a trip past P.maxTargetDist: take its nearest");
}

void one_slow_reset_does_not_make_a_farm() {
    std::vector<int> patch;
    auto w = medium_patch_board(patch);
    for (int t : patch)
        watchedResets(w, t, 1, 30);
    bot::Targets targets;
    targets.choosePearl(w);
    require(bot::Targets::yieldAt(w, patch[0]) == 1 && targets.pearl == -1,
            "tiles each seen resetting once, to 30, are not known to be rich");
}

void a_near_pearl_still_beats_a_far_medium_patch() {
    std::vector<int> patch;
    auto w = medium_patch_board(patch);
    for (int t : patch)
        watchedResets(w, t, 3, 12);
    int const near = w.tileOf(10, 8);
    test::pearl(w, near);
    bot::Targets targets;
    targets.choosePearl(w);
    require(targets.pearl == near, "a visible pearl two steps away is eaten first");
}

void a_fast_tile_counts_in_full_and_a_slower_one_by_its_rate() {
    auto w = test::world();
    int const t = w.tileOf(4, 4), fastNear = w.tileOf(5, 4), slowNear = w.tileOf(4, 5);
    watchedResets(w, t, 2, 1);
    watchedResets(w, fastNear, 2, 1);
    double const one = bot::Targets::yieldAt(w, t);
    require(one == 1 + P.farmClusterWeight, "a fast neighbour adds P.farmClusterWeight, as before");
    int const slowMean = static_cast<int>(4 * P.rateRefGap);
    watchedResets(w, slowNear, 2, slowMean);
    double const expected = one + (P.rateYield ? P.farmClusterWeight * P.rateRefGap / slowMean : 0);
    require(std::abs(bot::Targets::yieldAt(w, t) - expected) < 1e-9,
            "a neighbour resetting four times slower than P.rateRefGap adds a quarter as much");
}

// A W x H open board at round 5 where only our 7x7 view has ever been seen, our head at (hx, hy) facing `dir`.
bot::World blind(int width, int height, int hx, int hy, int dir) {
    auto w = test::world(width, height);
    w.round = 5;
    w.head = w.tileOf(hx, hy);
    w.facing[w.head] = dir;
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

// The tile `steps` along direction `dir` from t on the open torus.
int ahead(bot::World const& w, int t, int dir, int steps) {
    for (int i = 0; i < steps; i++)
        t = w.neighbour(t, dir);
    return t;
}

// On open unseen ground the grid tiles one stride ahead, left and right tie; the heading breaks the tie, where
// scan order used to send every blind dragon north, else west.
void blind_dragon_explores_straight_ahead() {
    for (int dir = 0; dir < 4; dir++) {
        auto w = blind(64, 64, 30, 30, dir);
        bot::Targets targets;
        targets.update(w);
        require(targets.pearl < 0, "nothing in view to eat");
        require(targets.explore == ahead(w, w.head, dir, P.exploreStride),
                "the explore target lies straight along our facing (dir " + std::to_string(dir) + ")");
    }
}

// Kelp across the board two rows ahead closes the way on: the target turns to the side, not behind.
void wall_ahead_turns_the_explore_target() {
    int const hx = 30, hy = 30;
    auto w = blind(64, 64, hx, hy, 2);
    for (int x = 0; x < w.W; x++) {
        test::wall(w, w.tileOf(x, hy + 1), 2);
        test::wall(w, w.tileOf(x, hy - 8), 0);
    }
    bot::Targets targets;
    targets.update(w);
    require(targets.explore >= 0, "there is unseen ground beside us");
    require(w.yOf(targets.explore) == hy && std::abs(w.xOf(targets.explore) - hx) == P.exploreStride,
            "with the way south shut the target is a stride to the side");
}

// A visible teammate on our heading still repels the target to the side.
void teammate_ahead_repels_the_explore_target() {
    auto w = blind(64, 64, 30, 30, 2);
    test::dragon(w, 7, false, w.tileOf(30, 33), 2);
    bot::Targets targets;
    targets.update(w);
    require(targets.explore >= 0 && w.yOf(targets.explore) == 30,
            "the teammate ahead pushes the target abeam of us");
}

// A tile half the torus away along our facing is as near either way round: it counts as straight ahead.
void half_map_away_counts_as_ahead() {
    auto w = test::world(16, 16);
    for (int hx : {4, 12}) {
        w.head = w.tileOf(hx, 8);
        w.facing[w.head] = 1;
        require(bot::Targets::headingShare(w, w.tileOf((hx + 8) % 16, 8)) == 1, "half the map east of us is ahead");
        require(bot::Targets::headingShare(w, w.tileOf(hx, 0)) == 0, "half the map north of us is abeam");
    }
}

// A pearl in view is eaten whatever our heading.
void pearl_in_view_beats_the_heading() {
    auto w = blind(64, 64, 30, 30, 2);
    int const pearl = w.tileOf(31, 29);
    test::pearl(w, pearl);
    bot::Targets targets;
    targets.update(w);
    require(targets.pearl == pearl, "the pearl behind us is still the target");
}


// Remembers teammate segments on rows y0..y1 of columns x-3..x+3 (fresh unless `age` says otherwise), without
// putting bodies in the way: only targets.hpp's crowding reads this memory.
void knot(bot::World& w, int x, int y0, int y1, int age = 0) {
    for (int y = y0; y <= y1; y++)
        for (int dx = -3; dx <= 3; dx++)
            w.friendBodySeen[w.tileOf((x + dx + w.W) % w.W, (y + w.H) % w.H)] = w.round - age;
}

// A long dragon (P.roomMinLength + 4) at the unit cap on an open 64 x 64 board (big enough for rescueReserve), head
// at (16, 16). Crowding counts from where ordinary splits stop, the cap less rescueReserve.
int swarmFull(bot::World const& w) { return w.unitLimit - bot::rescueReserve(w); }

bot::World crowded(int length = P.roomMinLength + 4) {
    auto w = test::world(64, 64);
    w.head = w.tileOf(16, 16);
    w.length = length;
    w.unitCount = w.unitLimit;
    return w;
}

// At the cap a long dragon takes the pearl 7 steps away in open ground over one 5 steps away inside a knot of
// teammate bodies; below where ordinary splits stop, or a dragon shorter than P.roomMinLength, takes the nearer one.
void at_the_cap_a_long_dragon_prefers_the_open_pearl() {
    int const near = crowded().tileOf(16, 21), open = crowded().tileOf(16, 9);
    auto const choose = [&](bot::World w) {
        test::pearl(w, near);
        test::pearl(w, open);
        knot(w, 16, 23, 24);
        bot::Targets targets;
        targets.update(w);
        return targets.pearl;
    };
    require(choose(crowded()) == open, "at the cap the long dragon goes where there is room");
    auto below = crowded();
    below.unitCount = swarmFull(below) - 1;
    require(choose(below) == near, "below where ordinary splits stop, the nearer pearl wins");
    auto full = crowded();
    full.unitCount = swarmFull(full);
    require(choose(full) == open, "where ordinary splits stop, short of the cap, the swarm is already full");
    if (!P.crowdByLength) {
        require(choose(crowded(P.roomMinLength - 1)) == near, "a dragon shorter than roomMinLength takes the nearer pearl");
        require(choose(crowded(P.roomMinLength)) == open, "a dragon roomMinLength long already counts crowding");
    }
}

// With P.crowdByLength the crowd's price grows with our length from any length: against one row of 7 teammate
// tiles round the nearer pearl (2 steps nearer), the shortest dragon for which 7 tiles cost more than 2 steps
// (Targets::crowdPrice) takes the open pearl, one shorter the nearer.
void crowd_price_grows_with_length() {
    if (!P.crowdByLength)
        return;
    int const near = crowded().tileOf(16, 21), open = crowded().tileOf(16, 9);
    auto const choose = [&](int length) {
        auto w = crowded(length);
        test::pearl(w, near);
        test::pearl(w, open);
        knot(w, 16, 23, 23);
        bot::Targets targets;
        targets.update(w);
        return targets.pearl;
    };
    int boundary = 2;
    while (7 * bot::Targets::crowdPrice(crowded(boundary)) <= 2)
        boundary++;
    require(boundary > 2 && boundary < P.roomMinLength, "the boundary falls below the old length cutoff");
    require(choose(boundary) == open, "at the boundary length the crowd outweighs 2 steps");
    require(choose(boundary - 1) == near, "one shorter, the nearer pearl wins");
}

// Bodies seen more than P.crowdMemoryRounds ago no longer count.
void old_sightings_do_not_crowd_a_pearl() {
    auto w = crowded();
    int const near = w.tileOf(16, 21), open = w.tileOf(16, 9);
    test::pearl(w, near);
    test::pearl(w, open);
    knot(w, 16, 23, 24, P.crowdMemoryRounds + 1);
    bot::Targets targets;
    targets.update(w);
    require(targets.pearl == near, "a knot last seen long ago is not counted");
}

// A pearl within P.crowdFreeDist is still taken however crowded; one step further it pays for the crowd.
void a_close_pearl_is_taken_despite_the_crowd() {
    for (int extra : {0, 1}) {
        auto w = crowded();
        int const dist = P.crowdFreeDist + extra;
        int const near = w.tileOf(16, 16 + dist), open = w.tileOf(16, 16 - dist - 2);
        test::pearl(w, near);
        test::pearl(w, open);
        knot(w, 16, 16 + dist + 2, 16 + dist + 3);
        bot::Targets targets;
        targets.update(w);
        require(targets.pearl == (extra == 0 ? near : open),
                extra == 0 ? "a pearl within crowdFreeDist is taken" : "one step further the crowd counts");
    }
}

// When every pearl is as crowded as the next, the order is the usual one: nearest first.
void equally_crowded_pearls_keep_the_usual_order() {
    auto w = crowded();
    int const near = w.tileOf(16, 21), far = w.tileOf(16, 9);
    test::pearl(w, near);
    test::pearl(w, far);
    knot(w, 16, 23, 24);
    knot(w, 16, 7, 8);
    bot::Targets targets;
    targets.update(w);
    require(targets.pearl == near, "equal crowds leave the nearer pearl first");
}

// At the cap the explore target also turns away from ground crowded with remembered teammate bodies; below
// a full swarm (the cap less rescueReserve) it goes straight ahead as before.
void at_the_cap_explore_avoids_crowded_ground() {
    for (bool atCap : {true, false}) {
        auto w = blind(64, 64, 30, 30, 2);
        w.length = P.roomMinLength;
        w.unitCount = atCap ? w.unitLimit : swarmFull(w) - 1;
        int const straight = ahead(w, w.head, 2, P.exploreStride);
        knot(w, 30, w.yOf(straight) + 2, w.yOf(straight) + 3);
        bot::Targets targets;
        targets.update(w);
        require(targets.explore >= 0, "there is unseen ground");
        require((targets.explore == straight) == !atCap,
                atCap ? "at the cap the target leaves the crowded ground ahead" : "below a full swarm it goes straight ahead");
    }
}
// A teammate's report (or our own view) of an enemy head on `tile` at `round`: one seen cell, the head's.
void reportEnemy(bot::Belief& b, int tile, int round, int now) {
    bot::Record r;
    r.round = round;
    r.where = b.blockOf(tile);
    int const x = tile % b.W - r.where % b.BW * bot::BLOCK, y = tile / b.W - r.where / b.BW * bot::BLOCK;
    r.seenMask = 1 << (y * bot::BLOCK + x);
    r.enemy = true;
    b.merge(r, now);
}

// A long dragon on an open 32 x 32 board, head at (16, 16), choosing between a pearl 5 steps north and one 6 steps
// south, with an enemy head reported `age` rounds ago beside the northern one (same 4 x 4 block).
int grazeChoice(int length, int age, bool report = true) {
    auto w = test::world(32, 32);
    w.head = w.tileOf(16, 16);
    w.length = length;
    int const north = w.tileOf(16, 11), south = w.tileOf(16, 22);
    test::pearl(w, north);
    test::pearl(w, south);
    bot::Belief belief;
    belief.init(w);
    if (report)
        reportEnemy(belief, w.tileOf(17, 10), w.round - age, w.round);
    bot::Targets targets;
    targets.team = &belief;
    targets.update(w);
    return targets.pearl == north ? 0 : targets.pearl == south ? 1 : -1;
}

// A 20-long dragon leaves the nearer pearl where a teammate saw an enemy head 2 rounds ago; with the report 40
// rounds old, or none, it takes the nearer one.
void long_dragon_grazes_away_from_a_fresh_enemy_sighting() {
    require(grazeChoice(20, 2) == 1, "a fresh enemy sighting beside the nearer pearl sends a long dragon to the other");
    require(grazeChoice(20, 40) == 0, "a sighting 40 rounds old has faded: the nearer pearl wins");
    require(grazeChoice(20, 2, false) == 0, "with no report the nearer pearl wins");
}

// Without P.grazeByLength grazing starts at P.grazeMinLength: one shorter, and a 6-long dragon, keep the nearer
// pearl. With it the price is our length over P.grazeRefLength times P.grazeWeight, from any length: a sighting
// costs a dragon twice as long twice as much, and a 2-long dragon takes the nearer pearl.
void grazing_grows_with_length() {
    if (!P.grazeByLength) {
        require(grazeChoice(P.grazeMinLength, 2) == 1, "a dragon grazeMinLength long already grazes");
        require(grazeChoice(P.grazeMinLength - 1, 2) == 0, "one shorter takes the nearer pearl");
        require(grazeChoice(6, 2) == 0, "a short dragon takes the nearer pearl");
        return;
    }
    auto const price = [](int length) {
        auto w = test::world(32, 32);
        w.length = length;
        bot::Belief belief;
        belief.init(w);
        reportEnemy(belief, w.tileOf(1, 1), w.round - 1, w.round);
        bot::Targets targets;
        targets.team = &belief;
        targets.prepareGraze(w, true);
        return targets.grazeAt(w.tileOf(1, 1));
    };
    require(price(6) > 0 && std::abs(price(12) - 2 * price(6)) < 1e-9, "the price is proportional to our length");
    require(grazeChoice(20, 2) == 1 && grazeChoice(2, 2) == 0, "a 20-long dragon grazes away, a 2-long one doesn't");
}

// A sighting counts over its block and the 8 around it (the map wraps), and no further.
void a_sighting_covers_its_neighbouring_blocks() {
    auto w = test::world(32, 32);
    w.length = P.grazeMinLength;
    bot::Belief belief;
    belief.init(w);
    reportEnemy(belief, w.tileOf(1, 1), w.round - 1, w.round);
    bot::Targets targets;
    targets.team = &belief;
    targets.prepareGraze(w, true);
    require(targets.grazeAt(w.tileOf(1, 1)) > 0 && targets.grazeAt(w.tileOf(30, 30)) == targets.grazeAt(w.tileOf(1, 1)) &&
                targets.grazeAt(w.tileOf(5, 29)) == targets.grazeAt(w.tileOf(1, 1)),
            "the reported block and its neighbours across the map's wrap cost the same");
    require(targets.grazeAt(w.tileOf(8, 1)) == 0 && targets.grazeAt(w.tileOf(1, 24)) == 0,
            "two blocks away costs nothing");
    targets.prepareGraze(w, false);
    require(targets.grazeAt(w.tileOf(1, 1)) == 0, "switched off it costs nothing");
}

// A blind long dragon's explore target turns away from ground ahead where an enemy head was seen 2 rounds ago; one
// shorter than P.grazeMinLength still goes straight ahead unless P.grazeByLength prices its length too.
void long_dragon_explores_away_from_a_fresh_enemy_sighting() {
    for (int length : {P.grazeMinLength, P.grazeMinLength - 1}) {
        auto w = blind(64, 64, 30, 30, 2);
        w.length = length;
        int const straight = ahead(w, w.head, 2, P.exploreStride);
        bot::Belief belief;
        belief.init(w);
        reportEnemy(belief, w.tileOf(w.xOf(straight) + 1, w.yOf(straight) + 1), w.round - 2, w.round);
        bot::Targets targets;
        targets.team = &belief;
        targets.update(w);
        require(targets.explore >= 0, "there is unseen ground");
        if (length >= P.grazeMinLength || P.grazeByLength)
            require(targets.explore != straight && targets.grazeAt(targets.explore) == 0,
                    "a long dragon explores where no enemy head was seen");
        else
            require(targets.explore == straight, "a short dragon goes straight ahead");
    }
}
} // namespace

int main() {
    return test::suite({
        {"visible pearl selects shortest reachable target", visible_pearl_selects_shortest_reachable_target},
        {"remembered pearl expires", remembered_pearl_is_usable_until_it_becomes_stale},
        {"empty observation invalidates pearl memory", later_empty_observation_invalidates_remembered_pearl},
        {"visible pearl beats equally close memory", visible_pearl_beats_equally_close_unseen_pearl},
        {"countdown target starts on the move after spawn", countdown_target_uses_first_move_after_spawn},
        {"a spawn wait is worth what exploring costs this dragon", spawn_wait_is_worth_what_exploring_costs_this_dragon},
        {"elapsed countdown respects later empty observations", elapsed_countdown_requires_no_subsequent_empty_sighting},
        {"equal teammates use id ownership", equal_arrival_teammates_use_id_to_assign_one_owner},
        {"closer teammate wins regardless of id", closer_teammate_owns_pearl_regardless_of_id},
        {"enemy ownership requires strictly earlier arrival", enemy_ownership_requires_a_strictly_earlier_arrival},
        {"spawn waiting can create an ownership tie", countdown_wait_can_turn_distance_advantage_into_id_tie},
        {"claimed pearl leaves another target choice", teammate_claimed_nearest_pearl_leaves_another_choice},
        {"E0022 counterexample keeps separate nearest pearls", nearest_teammates_keep_separate_pearls_in_e0022_counterexample},
        {"unreachable pearl does not hide reachable food", unreachable_nearest_pearl_does_not_hide_reachable_food},
        {"equally good pearls go along our heading", equally_good_pearls_go_along_our_heading},
        {"equal spawns go along our heading, a sooner one wins", equal_spawns_go_along_our_heading_but_a_sooner_one_wins},
        {"headless body does not invent a pearl owner", headless_visible_body_does_not_invent_a_pearl_owner},
        {"vanished food falls back to stable exploration", vanished_pearl_resets_selection_and_exploration_retargets_when_seen},
        {"fully seen empty world has no target", fully_seen_empty_world_has_no_target},
        {"fast tile cluster outranks nearer lone pearl", fast_tile_cluster_outranks_a_nearer_lone_pearl},
        {"out-of-sight fast tile stays a target", out_of_sight_fast_tile_stays_a_target},
        {"farm behind a long detour is worth the trip", farm_behind_a_long_detour_is_worth_the_trip},
        {"explore episodes count the walk to what they found", explore_episodes_count_the_walk_to_what_they_found},
        {"pearl reach follows what exploring costs this dragon", pearl_reach_follows_what_exploring_costs_this_dragon},
        {"a patch of medium-rate tiles is worth a long trip", a_patch_of_medium_rate_tiles_is_worth_a_long_trip},
        {"one slow reset does not make a farm", one_slow_reset_does_not_make_a_farm},
        {"a near pearl still beats a far medium patch", a_near_pearl_still_beats_a_far_medium_patch},
        {"a fast tile counts in full and a slower one by its rate", a_fast_tile_counts_in_full_and_a_slower_one_by_its_rate},
        {"blind dragon explores straight ahead", blind_dragon_explores_straight_ahead},
        {"wall ahead turns the explore target", wall_ahead_turns_the_explore_target},
        {"teammate ahead repels the explore target", teammate_ahead_repels_the_explore_target},
        {"pearl in view beats the heading", pearl_in_view_beats_the_heading},
        {"half the map away counts as ahead", half_map_away_counts_as_ahead},
        {"at the cap a long dragon prefers the open pearl", at_the_cap_a_long_dragon_prefers_the_open_pearl},
        {"crowd price grows with length", crowd_price_grows_with_length},
        {"old sightings do not crowd a pearl", old_sightings_do_not_crowd_a_pearl},
        {"a close pearl is taken despite the crowd", a_close_pearl_is_taken_despite_the_crowd},
        {"equally crowded pearls keep the usual order", equally_crowded_pearls_keep_the_usual_order},
        {"at the cap explore avoids crowded ground", at_the_cap_explore_avoids_crowded_ground},
        {"long dragon grazes away from a fresh enemy sighting", long_dragon_grazes_away_from_a_fresh_enemy_sighting},
        {"grazing grows with length", grazing_grows_with_length},
        {"a sighting covers its neighbouring blocks", a_sighting_covers_its_neighbouring_blocks},
        {"long dragon explores away from a fresh enemy sighting", long_dragon_explores_away_from_a_fresh_enemy_sighting},
    });
}
