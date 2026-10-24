#include "support.hpp"
#include "ram.hpp"
#include "scoring.hpp"
#include "targets.hpp"

using test::require;

// Harvesting drops (I747, from the gauntlet `harvester` entry): a pearl a death dropped is contested, never yielded to
// a head that gets there first, a fight's drop outweighs a nearer lone pearl (P.harvestDrops), an enemy head in a fight
// is a target (P.harvestFights), a drop beside no longer enemy head is stepped onto despite the ram guard
// (P.harvestStep), and with P.harvestRamGate a ram needs our other heads to reach more of its drop first.
namespace {

unswbc::Tile segment(int x, int y, int id, bool enemy, bool head) {
    unswbc::Position const position{x, y};
    return {position,
            unswbc::DragonPart(position, id, enemy ? unswbc::Team::B : unswbc::Team::A, unswbc::Direction::EAST, head),
            -1};
}

// Feeds real controller snapshots through World::observe without protocol I/O.
struct Observations {
    unswbc::Game game{12, 12, 64};
    unswbc::Controller controller{1, unswbc::Team::A, unswbc::Direction::EAST, {}, 64};
    bot::World w;

    Observations() { w.init(controller, game); }

    void observe(int round, std::vector<unswbc::Tile> tiles) {
        game.round_num = round;
        controller.length = 2;
        controller.head.position = {5, 5};
        controller.vision = unswbc::Vision(std::move(tiles));
        w.observe(controller, game);
    }
};

// World::dropNow: a pearl where a dragon's segment lay when we last saw the tile is a drop; one on a tile we last saw
// empty is not, and a drop stops being one once eaten.
void dropsAreReadFromWhatTheTileHeld() {
    Observations scene;
    unswbc::Tile const emptyTile{{7, 5}, std::nullopt, -1, false};
    scene.observe(100, {segment(5, 5, 1, false, true), segment(6, 4, 9, true, false), emptyTile});
    scene.observe(101, {segment(5, 5, 1, false, true), {{6, 4}, std::nullopt, -1, true}, {{7, 5}, std::nullopt, -1, true}});
    require(scene.w.dropNow(scene.w.tileOf(6, 4)), "a pearl where an enemy segment lay is a drop");
    require(!scene.w.dropNow(scene.w.tileOf(7, 5)), "a pearl on a tile last seen empty is not");
    scene.observe(102, {segment(5, 5, 1, false, true), {{6, 4}, std::nullopt, -1, true}});
    require(scene.w.dropNow(scene.w.tileOf(6, 4)), "an uneaten drop stays one");
    scene.observe(103, {segment(5, 5, 1, false, true), {{6, 4}, std::nullopt, -1, false}});
    require(!scene.w.dropNow(scene.w.tileOf(6, 4)), "once eaten it is gone");
}

void drop(bot::World& w, int tile) {
    test::pearl(w, tile);
    w.dropSeen[tile] = w.round;
}

void dropIsContestedWhenAnEnemyIsCloser() {
    auto w = test::world();
    int const pearl = w.tileOf(11, 8);                  // three steps east of our head at (8,8)
    test::dragon(w, 2, true, w.tileOf(12, 8), 3);      // one step from it
    test::pearl(w, pearl);
    bot::Targets targets;
    targets.update(w);
    require(P.yieldToEnemyCloser && targets.pearl != pearl, "an ordinary pearl the enemy reaches first is yielded");
    w.dropSeen[pearl] = w.round;
    targets.update(w);
    require(targets.pearl == pearl, "the same pearl as a drop is contested");
}

void dropIsContestedWhenATeammateIsCloser() {
    auto w = test::world();
    int const pearl = w.tileOf(11, 8);
    test::dragon(w, 5, false, w.tileOf(12, 8), 3);
    drop(w, pearl);
    bot::Targets targets;
    targets.update(w);
    require(targets.pearl == pearl, "a drop a teammate reaches first is still ours to contest");
}

void fightsDropBeatsANearerLonePearl() {
    auto w = test::world();
    int const lone = w.tileOf(8, 6);                     // two steps north
    test::pearl(w, lone);
    for (int t : {w.tileOf(11, 9), w.tileOf(12, 9), w.tileOf(11, 10)}) // a fight's drop, 4-5 steps away
        drop(w, t);
    bot::Targets targets;
    targets.update(w);
    require(targets.pearl == w.tileOf(11, 9), "three drop pearls together outweigh a lone pearl two steps nearer");
    for (int t : {w.tileOf(11, 9), w.tileOf(12, 9), w.tileOf(11, 10)})
        w.dropSeen[t] = -1;
    targets.update(w);
    require(targets.pearl == lone, "without the drops the nearer pearl wins");
}

void waitsAtAFight() {
    auto w = test::world();
    int const enemy = w.tileOf(12, 8);                  // four steps east
    test::dragon(w, 2, true, enemy, 6);
    bot::Targets targets;
    targets.update(w);
    require(targets.pearl < 0, "a lone enemy head is no target");
    test::dragon(w, 5, false, w.tileOf(12, 10), 3);    // one of ours two steps from it: a fight
    targets.update(w);
    require(targets.pearl == enemy, "an enemy head in a fight is a target worth its drop");
}

void ramsOnlyWhereItEatsTheDrop() {
    auto w = test::world();
    test::body(w, {w.tileOf(8, 8), w.tileOf(7, 8), w.tileOf(6, 8), w.tileOf(5, 8)});
    test::dragon(w, 2, true, w.tileOf(10, 8), 6); // two free steps east, longer than us
    bot::Safety safety;
    safety.prepare(w);
    require(bot::findRam(w, safety).steps.empty() == P.harvestRamGate,
            "with no head of ours near the drop the longer head is rammed only without P.harvestRamGate");
    test::dragon(w, 5, false, w.tileOf(10, 6), 2); // ours, two steps from the victim's tile
    safety.prepare(w);
    require(bot::findRam(w, safety).steps == std::vector<int>{1, 1}, "with a teammate to eat the drop it is rammed");
}

// Score of our one-step move onto `dest` with no targets (the move scorer's terms alone).
double stepScore(bot::World const& w, int dest) {
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    for (int dir = 0; dir < 4; dir++)
        if (w.step(w.head, dir) == dest)
            return bot::evaluateMove(w, safety, targets, dir).score;
    return 0;
}

// Our length 4 heads east at (8,8), a pearl at (9,8) and an enemy head of `theirLen` beside it at (10,8), with a
// second enemy head near its drop: the ram guard marks the pearl's tile risky (at 4 the head is as long as us now and
// shorter once we eat, which the ram model still prices as a bad ram on us there).
bot::World pearlBesideEnemyHead(int theirLen, bool dropped) {
    auto w = test::world();
    test::body(w, {w.tileOf(8, 8), w.tileOf(7, 8), w.tileOf(6, 8), w.tileOf(5, 8)});
    test::dragon(w, 2, true, w.tileOf(10, 8), theirLen);
    test::dragon(w, 3, true, w.tileOf(10, 10), 2);
    test::pearl(w, w.tileOf(9, 8));
    if (dropped)
        w.dropSeen[w.tileOf(9, 8)] = w.round;
    return w;
}

void stepsOntoADropBesideNoLongerHead() {
    int const pearl = test::world().tileOf(9, 8);
    auto plain = pearlBesideEnemyHead(4, false);
    bot::Safety safety;
    safety.prepare(plain);
    require(safety.riskyFor(pearl), "the scenario: a head as long as us makes the pearl risky");
    require(stepScore(pearlBesideEnemyHead(4, true), pearl) - stepScore(plain, pearl) >= P.riskPenalty / 2,
            "as a drop, the pearl beside a head no longer than us is stepped onto without the ram guard's penalty");
    auto longer = pearlBesideEnemyHead(6, false);
    safety.prepare(longer);
    require(safety.riskyFor(pearl), "the scenario: a longer head's ram on us is risky too");
    require(stepScore(pearlBesideEnemyHead(6, true), pearl) - stepScore(longer, pearl) < P.riskPenalty / 2,
            "a drop beside a longer head keeps the ram guard's penalty");
}

} // namespace

int main() {
    return test::suite({
        {"a drop an enemy reaches first is contested", dropIsContestedWhenAnEnemyIsCloser},
        {"a drop a teammate reaches first is contested", dropIsContestedWhenATeammateIsCloser},
        {"a fight's drop beats a nearer lone pearl", fightsDropBeatsANearerLonePearl},
        {"waits at a fight for its drop", waitsAtAFight},
        {"rams only where it eats the drop, with the ram gate", ramsOnlyWhereItEatsTheDrop},
        {"drops are read from what the tile held", dropsAreReadFromWhatTheTileHeld},
        {"steps onto a drop beside no longer head", stepsOntoADropBesideNoLongerHead},
    });
}
