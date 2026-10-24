#include "support.hpp"
#include "capfeed.hpp"

namespace {


// A `size` x `size` board (40: big enough for the rescue reserve and the king seed) with the team at the ceiling where ordinary splits stop, a round
// before the fold starts (set from the board, as main does each turn): our dragon (id 5) of `length`, head at (20,20) and body trailing west, and our king (id 2)
// of visible length `kingLen` two steps east at (22,20). No enemy, no pearl. `intake` has eaten nothing for
// P.intakeWindow turns.
struct Scene {
    bot::World w;
    bot::Belief b;
    bot::Intake intake;
};

Scene scene(int length = 3, int kingLen = 20, int size = 40) {
    Scene s{test::world(size, size), {}, {}};
    s.w.myId = 5;
    bot::startFold(s.w);
    s.w.round = bot::foldStart() - 1;
    std::vector<int> tiles;
    for (int i = 0; i < length; ++i)
        tiles.push_back(s.w.tileOf(20 - i, 20));
    test::body(s.w, tiles);
    s.w.unitCount = s.w.unitLimit - bot::rescueReserve(s.w);
    test::dragon(s.w, 2, false, s.w.tileOf(22, 20), kingLen);
    s.b.init(s.w);
    s.intake.eaten.assign(P.intakeWindow, 0);
    return s;
}

bot::CapFeed capFeedAnswer(Scene const& s) {
    bot::DistField field;
    return bot::capFeedDirection(s.w, s.b, s.intake, field);
}

int capFeedFor(Scene const& s) { return capFeedAnswer(s).dir; }

// Our unit counts on P.intakeWindow + 1 turns up to this one, rising `perRound` a round to the scene's count.
void unitHistory(Scene& s, int perRound) {
    s.intake.units.clear();
    for (int i = P.intakeWindow; i >= 0; --i)
        s.intake.units.emplace_back(s.w.round - i, s.w.unitCount - perRound * i);
}

// True when stepping `dir` kills us in place: kelp or our own body.
bool diesAlone(bot::World const& w, int dir) {
    int const to = w.step(w.head, dir);
    return to == bot::KELP || (to >= 0 && w.occId[to] == w.myId && !w.occHead[to]);
}

bot::Record kingReport(int id, int length, int round, int where) {
    bot::Record r;
    r.king = true;
    r.round = round;
    r.where = where;
    r.length = length;
    r.id = id;
    return r;
}

void feedsTheKingAtTheCeiling() {
    auto const s = scene();
    test::require(P.capFeedShare * 2 >= P.capFeedFoldShare * 2 + P.capFeedMargin,
                  "precondition: with no growth left a 3-long dragon's drop is worth more eaten now");
    int const dir = capFeedFor(s);
    test::require(dir >= 0 && diesAlone(s.w, dir),
                  "at the ceiling, a 3-long dragon beside a 20-long king dies into kelp or its own body");
    test::require(s.w.step(s.w.head, dir) != s.w.tileOf(22, 20), "never into the king's head");
}

void belowTheCeilingPricesTheUnit() {
    // A 2-long dragon eating a pearl in 20 rounds, 20 rounds before the fold, one slot under the ceiling on a map the
    // king seed runs on: its drop now beats its alive value (capFeedPays at the ceiling) by less than a unit the team
    // takes all 20 rounds to replace, and by more than one it replaces in a round.
    auto s = scene(2, 20);
    s.w.round = bot::foldStart() - 20;
    s.w.unitCount -= 1;
    s.intake.eaten.assign(P.intakeWindow, 0);
    s.intake.eaten.back() = 1;
    test::require(bot::kingSeedOn(s.w), "precondition: the king seed runs on this map and round");
    test::require(bot::capFeedPays(2, s.intake.rate(), 20), "precondition: at the ceiling it would feed");
    unitHistory(s, 0);
    test::require(capFeedFor(s) < 0, "a team that isn't growing keeps the unit: no feed");
    unitHistory(s, 1);
    test::require(capFeedFor(s) >= 0, "a team growing a unit a round refills the slot at once: it feeds");

    auto small = scene(2, 20, 32);
    small.w.round = s.w.round;
    small.w.unitCount -= 1;
    small.intake = s.intake;
    test::require(small.w.N < P.kingSeedArea, "precondition: a map under the king seed's area");
    test::require(!bot::kingSeedOn(small.w) && capFeedFor(small) < 0,
                  "under the ceiling where the king seed is off, no feed however it would pay");
}

void holdsWhileGrowthLiesAhead() {
    auto s = scene();
    s.w.round = bot::foldStart() - 100;
    s.intake.eaten.assign(P.intakeWindow, 1);
    test::require(capFeedFor(s) < 0, "a dragon eating a pearl a round with 100 rounds to the fold is worth more alive");
    s.intake.eaten.assign(P.intakeWindow, 0);
    test::require(capFeedFor(s) >= 0, "the same dragon eating nothing feeds");
}

void enemyInViewRacesForTheDrop() {
    auto far = scene();
    test::dragon(far.w, 9, true, far.w.tileOf(20, 24), 2);
    test::require(capFeedFor(far) >= 0, "a 2-long enemy head 4 steps off can't beat the king to either drop tile");
    auto near = scene();
    test::dragon(near.w, 9, true, near.w.tileOf(20, 22), 2);
    test::require(capFeedFor(near) < 0, "one as close to both drop tiles as the king may take the drop");
}

// Takes the scene's king (id 2 at (22,20)) off the board.
void removeKing(Scene& s) {
    s.w.occId[s.w.tileOf(22, 20)] = -1;
    s.w.occHead[s.w.tileOf(22, 20)] = 0;
    s.w.dragons.pop_back();
}

void walksToAFarKing() {
    // The king in view 6 steps off: every gate but the distance passes, so it walks there.
    auto s = scene();
    removeKing(s);
    test::dragon(s.w, 2, false, s.w.tileOf(23, 23), 20);
    s.w.round = bot::foldStart() - 10;
    auto const walk = capFeedAnswer(s);
    test::require(walk.dir < 0 && walk.approach == s.w.tileOf(23, 23), "a feeder 6 steps from the king walks to it");
    s.w.round = bot::foldStart() - 5;
    test::require(capFeedAnswer(s).approach < 0, "not when it would arrive after the fold starts");

    auto enemy = s;
    enemy.w.round = bot::foldStart() - 10;
    test::dragon(enemy.w, 9, true, enemy.w.tileOf(17, 17), 2);
    test::require(capFeedAnswer(enemy).approach < 0, "an enemy head in view: no walk");

    // Out of view, known only from a fresh king report.
    auto reported = scene();
    reported.w.round = bot::foldStart() - 30;
    removeKing(reported);
    reported.b.mergeKing(kingReport(2, 30, reported.w.round, reported.w.tileOf(35, 20)));
    test::require(capFeedAnswer(reported).approach == reported.w.tileOf(35, 20), "walks to a reported king out of view");

    // A report older than our path to where it was: the king may have drifted as far, so it isn't walked to.
    auto stale = scene();
    stale.w.round = bot::foldStart() - 30;
    removeKing(stale);
    stale.b.mergeKing(kingReport(2, 30, stale.w.round - 6, stale.w.tileOf(25, 20)));
    test::require(6 > P.driftFactor * 5 && capFeedAnswer(stale).approach < 0,
                  "a report 6 rounds old 5 steps away isn't walked to");
}

void diesInPlaceWithNoDeathStep() {
    // Our body out of sight (only the head tracked, as across a portal), no kelp: the illegal split is the only death.
    auto s = scene();
    for (int i = 1; i < 3; ++i) {
        s.w.occId[s.w.tileOf(20 - i, 20)] = -1;
        s.w.occHead[s.w.tileOf(20 - i, 20)] = 0;
    }
    s.w.body.resize(1);
    test::require(capFeedFor(s) == bot::CapFeed::DIE_IN_PLACE, "with no kelp or own-body step it dies in place");
}

void feedsOnlyTheLongestKnownAtTheRatio() {
    auto shortKing = scene(3, 3 * P.capFeedKingRatio - 1);
    test::require(capFeedFor(shortKing) < 0, "a teammate under the ratio times our length isn't the king");
    auto atRatio = scene(3, 3 * P.capFeedKingRatio);
    test::require(capFeedFor(atRatio) >= 0, "one at exactly the ratio is");

    auto reported = scene();
    reported.b.mergeKing(kingReport(7, 30, reported.w.round, reported.w.tileOf(5, 5)));
    test::require(capFeedFor(reported) < 0, "a fresh report of a longer king elsewhere: this teammate isn't the king");
    auto self = scene(3, 3 * P.capFeedKingRatio - 1);
    self.b.mergeKing(kingReport(2, 30, self.w.round, self.w.tileOf(22, 20)));
    test::require(capFeedFor(self) >= 0, "a report of the teammate beside us counts as its length");

    auto longer = scene();
    test::dragon(longer.w, 3, false, longer.w.tileOf(20, 26), 25);
    test::require(capFeedFor(longer) < 0, "a longer teammate in view out of reach is the king, not the one beside us");

    auto shortSight = scene(3, 7);
    test::dragon(shortSight.w, 3, false, shortSight.w.tileOf(20, 26), 8);
    shortSight.b.mergeKing(kingReport(2, 30, shortSight.w.round, shortSight.w.tileOf(22, 20)));
    test::require(capFeedFor(shortSight) >= 0,
                  "the king beside us, reported 30 long but showing 7, beats a farther teammate showing 8");
}

void oneFeedAtATime() {
    // A second feeder beside the same king after the first died: its drop still lies within reach of the king.
    auto s = scene();
    s.w.round = bot::foldStart() - 10;
    test::pearl(s.w, s.w.tileOf(23, 21));
    s.w.touched.push_back(s.w.tileOf(23, 21));
    test::require(capFeedFor(s) < 0 && capFeedAnswer(s).approach == s.w.tileOf(22, 20),
                  "while the king has a drop to eat beside it, the next feeder waits by it");

    auto rival = scene();
    test::dragon(rival.w, 3, false, rival.w.tileOf(20, 22), 2);
    test::require(capFeedFor(rival) < 0, "another head as close to our head as the king could eat the drop");
}

void intakeAddsBackWhatItSpent() {
    auto w = test::world();
    bot::Intake intake;
    w.length = 4;
    intake.observe(w);
    w.length = 5; // ate one
    intake.observe(w);
    intake.spent(2); // split a 2-long child off
    w.length = 3;
    intake.observe(w);
    test::require(intake.eaten.size() == 2 && intake.eaten[0] == 1 && intake.eaten[1] == 0,
                  "a split's lost length is not read as negative intake");
    double const expected = (1 + P.capFeedTeamGrowth * P.intakePriorTurns) / (2 + P.intakePriorTurns);
    test::require(std::abs(intake.rate() - expected) < 1e-9, "a young dragon's record is shrunk to the team mean");
}

// I991's scenario: a 40-turn-old dragon that ate 4 pearls in its first 10 turns and none since reads nothing over its
// last P.intakeWindow turns, and above the team mean over its life, (4 + mean x prior) / (40 + prior).
void intakeReadsTheWholeLife() {
    bot::Intake intake;
    test::require(P.intakeByLife && P.intakeWindow <= 30, "precondition: the life estimate, a window inside the idle 30");
    test::require(std::abs(intake.rate() - P.capFeedTeamGrowth) < 1e-9, "a newborn reads the team mean");
    intake.eaten.assign(40, 0);
    for (int i = 0; i < 4; i++)
        intake.eaten[i] = 1;
    double const life = (4 + P.capFeedTeamGrowth * P.intakePriorTurns) / (40 + P.intakePriorTurns);
    test::require(std::abs(intake.rate() - life) < 1e-9 && life > P.capFeedTeamGrowth, "its whole record counts");
}

} // namespace

int main() {
    return test::suite({
        {"feeds the king at the ceiling", feedsTheKingAtTheCeiling},
        {"intake reads the whole life", intakeReadsTheWholeLife},
        {"below the ceiling prices the unit", belowTheCeilingPricesTheUnit},
        {"holds while growth lies ahead", holdsWhileGrowthLiesAhead},
        {"an enemy in view races for the drop", enemyInViewRacesForTheDrop},
        {"walks to a far king", walksToAFarKing},
        {"dies in place with no death step", diesInPlaceWithNoDeathStep},
        {"feeds only the longest known at the ratio", feedsOnlyTheLongestKnownAtTheRatio},
        {"one feed at a time", oneFeedAtATime},
        {"intake adds back what it spent", intakeAddsBackWhatItSpent},
    });
}
