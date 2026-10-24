#include "support.hpp"
#include "escape.hpp"
#include "split.hpp"

namespace {

// Whether enemy head d rams our head this turn with every sprint the full model gives it.
bool reaches(bot::World const& w, bot::SeenDragon const& d) {
    return bot::fullEnemyReaches(w, d, w.head, bot::enemyReach(w, d, true));
}

void longEnemyReach() {
    auto w = test::world();
    test::dragon(w, 2, true, w.tileOf(8, 3), 6);
    auto const& enemy = w.dragons.back();
    test::require(!bot::enemyReaches(w, enemy.head, w.head, -1, 0, bot::enemyReach(w, enemy, false)),
                  "the original cap misses the five-step ram");
    test::require(reaches(w, enemy),
                  "a six-long enemy can ram a head five free steps away");
}

void pearlFundsShedThreat() {
    auto w = test::world();
    test::body(w, {w.tileOf(8, 8), w.tileOf(7, 8), w.tileOf(6, 8), w.tileOf(5, 8), w.tileOf(4, 8), w.tileOf(3, 8)});
    test::dragon(w, 2, true, w.tileOf(8, 4), 3);
    test::pearl(w, w.tileOf(8, 5));
    unswbc::Controller ct(w.myId, unswbc::Team::A, unswbc::Direction::EAST, {}, w.unitLimit);
    ct.length = w.length;
    ct.unit_count = w.unitCount;
    bot::Safety safety;
    safety.prepare(w);
    bot::DistField field;
    test::require(bot::shedLength(w, ct, safety, field, false, false) == 0, "switch-off misses the pearl-funded threat");
    test::require(bot::shedLength(w, ct, safety, field, false, true) == w.length - unswbc::Constants::MIN_SIZE,
                  "a pearl funds the extra sprint step before shed slack");
    w.pearlNow[w.tileOf(8, 5)] = 0;
    w.dragons.back().visibleLen = 2;
    test::require(bot::shedLength(w, ct, safety, field, false, true) == 0, "a confirmed short enemy is out of reach");
    w.visStamp[w.tileOf(8, 3)] = 0;
    test::require(bot::shedLength(w, ct, safety, field, false, true) == w.length - unswbc::Constants::MIN_SIZE,
                  "an unseen enemy tail uses the same fallback in the shed as in the escape");
    test::require(bot::shedLength(w, ct, safety, field, false, false) == 0, "switch-off retains the shed's old short estimate");
}

void pearlTiming() {
    auto w = test::world(32, 32);
    test::dragon(w, 2, true, w.tileOf(16, 13), 3);
    auto const enemy = w.dragons.back();
    test::require(!reaches(w, enemy), "three steps need another segment");
    test::pearl(w, w.head);
    test::require(!reaches(w, enemy), "a pearl at the target cannot fund its collision step");
    test::pearl(w, w.tileOf(16, 14));
    test::require(reaches(w, enemy), "a pearl before the last step funds it");
    w.pearlNow[w.tileOf(16, 14)] = 0;
    auto shortEnemy = enemy;
    shortEnemy.visibleLen = 2;
    test::pearl(w, w.tileOf(16, 15));
    test::require(!reaches(w, shortEnemy), "a pearl beyond the first unpaid step comes too late");
}

// The engine's rule (docs/unsw/execution-order.md, "Applying a move"): step j of a sprint is paid when
// L + (pearls eaten before step j) - (j - 2) >= 3. A 4-long head 4 steps away needs one pearl on steps 1-3; one on
// the 4th tile comes after the check that kills it.
void pearlMustComeBeforeTheStepItPays() {
    auto w = test::world(32, 32);
    test::dragon(w, 2, true, w.tileOf(16, 12), 4);
    auto const enemy = w.dragons.back();
    int const end = w.tileOf(16, 16);
    w.occId[end] = -1;
    auto const reachesEnd = [&] { return bot::fullEnemyReaches(w, enemy, end, bot::enemyReach(w, enemy, true)); };
    test::require(!reachesEnd(), "a 4-long head sprints 3 steps unpaid, not 4");
    test::pearl(w, end);
    test::require(!reachesEnd(), "a pearl on the 4th tile pays nothing");
    test::pearl(w, w.tileOf(16, 14));
    test::require(reachesEnd(), "a pearl on the 2nd tile pays for the 4th step");
    w.pearlNow[w.tileOf(16, 14)] = 0;
    test::pearl(w, w.tileOf(16, 15));
    test::require(reachesEnd(), "a pearl on the 3rd tile pays for the 4th step");
}

void shortEnemyCanEatFirst() {
    auto w = test::world();
    test::dragon(w, 2, true, w.tileOf(8, 6), 2);
    auto const enemy = w.dragons.back();
    test::require(!reaches(w, enemy), "a two-long head has only its ordinary step");
    test::pearl(w, w.tileOf(8, 7));
    test::require(reaches(w, enemy), "a pearl on its first free step enables a second step");
}

void blockedBodiesAndVacatedTail() {
    auto w = test::world();
    test::dragon(w, 2, true, w.tileOf(8, 6), 3);
    auto const enemy = w.dragons.back();
    int const middle = w.tileOf(8, 7);
    w.occId[middle] = w.myId;
    w.occK[middle] = 0;
    test::require(!reaches(w, enemy), "our standing body blocks the short route");
    test::require(bot::fullEnemyReaches(w, enemy, w.head, bot::enemyReach(w, enemy, true), -1, 1), "an escape's vacated tail is free");
    test::require(!bot::fullEnemyReaches(w, enemy, w.head, bot::enemyReach(w, enemy, true), middle, 1), "the escape's new neck stays blocked");
}

void partialVisionAndLegacy() {
    auto w = test::world();
    test::dragon(w, 2, true, w.tileOf(8, 5), 2);
    auto const enemy = w.dragons.back();
    test::require(bot::enemyReach(w, enemy, true) == 1, "confirmed short chain reaches one step");
    w.visStamp[w.tileOf(8, 4)] = 0;
    test::require(bot::enemyReach(w, enemy, true) == P.maxRamSteps, "an unconfirmed tail keeps the escape fallback");
    test::require(bot::enemyReach(w, enemy, true, false) == 1, "without the fallback (shedUnseenTailReach off) "
                                                               "the visible length decides");
    auto longer = enemy;
    longer.visibleLen = P.maxRamSteps + 3;
    test::require(bot::enemyReach(w, longer, true) == longer.visibleLen - 1, "fallback cannot shorten observed length");
    test::require(bot::enemyReach(w, longer, false) == P.maxRamSteps, "switch-off retains the original fallback");
    w.visStamp[w.tileOf(8, 4)] = w.stamp;
    test::require(bot::enemyReach(w, longer, false) == std::min(P.maxRamSteps, longer.visibleLen - 1),
                  "switch-off retains the original known-length cap");
}

void portalAndSightBoundary() {
    auto w = test::world();
    test::dragon(w, 2, true, w.tileOf(2, 2), 3);
    auto const enemy = w.dragons.back();
    int const landing = w.tileOf(8, 7);
    w.stepTo[4 * enemy.head + 1] = landing;
    test::require(reaches(w, enemy), "a known portal counts as one path step");
    w.visStamp[landing] = 0;
    test::require(reaches(w, enemy), "escape retains its unseen-ground assumption");
    test::require(!bot::fullEnemyReaches(w, enemy, w.head, bot::enemyReach(w, enemy, true), -1, 0, true), "shed retains its visible-path boundary");
}

} // namespace

int main() {
    return test::suite({
        {"long enemy reach", longEnemyReach},
        {"pearl-funded shed threat", pearlFundsShedThreat},
        {"pearl timing", pearlTiming},
        {"a pearl pays only for later steps", pearlMustComeBeforeTheStepItPays},
        {"two-long first pearl", shortEnemyCanEatFirst},
        {"body timing", blockedBodiesAndVacatedTail},
        {"partial vision and switch-off", partialVisionAndLegacy},
        {"portals and vision", portalAndSightBoundary},
    });
}
