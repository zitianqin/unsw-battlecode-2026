#include "support.hpp"
#include "feed.hpp"
#include "fold.hpp"
#include "scoring.hpp"

namespace {

// Round P.mergeRound + 20, 30 units: our dragon (id `ourId`) of `length` with its head at (8,8) and body trailing west,
// and a teammate (id `mateId`) of visible length `mateLen` with its head at (mateX, 8), two steps east by
// default. No enemy in view.
bot::World mergeScene(int length = 3, int mateLen = 3, int ourId = 5, int mateId = 2, int mateX = 10) {
    auto w = test::world();
    w.myId = ourId;
    std::vector<int> tiles;
    for (int i = 0; i < length; ++i)
        tiles.push_back(w.tileOf(8 - i, 8));
    test::body(w, tiles);
    w.round = P.mergeRound + 20;
    w.unitCount = 30;
    test::dragon(w, mateId, false, w.tileOf(mateX, 8), mateLen);
    return w;
}

// At `round` (15 rounds into the fold, foldStart, by default), 30 units, a 32x32 board: our length-16 dragon, king as
// far as it knows (no longer teammate in view, no king report), with its head at (20,8), body trailing west, and a
// length-3 teammate head five steps south at (20,13). No enemy, no pearl.
bot::World kingScene(int round = bot::foldStart() + 15) {
    auto w = test::world(32, 32);
    std::vector<int> tiles;
    for (int i = 0; i < 16; ++i)
        tiles.push_back(w.tileOf(20 - i, 8));
    test::body(w, tiles);
    w.round = round;
    w.unitCount = 30;
    test::dragon(w, 2, false, w.tileOf(20, 13), 3);
    return w;
}

// A turn's targeting for `w` as main runs it: seekFeeder, asked for a fold king by kingSeeksInFold, after
// Targets::update. With `heardLen` the belief holds one king report of teammate 9 that long, aged out
// (older than P.kingReportAge), else none.
struct KingTurn {
    bot::Safety safety;
    bot::Targets targets;
    bool sought = false;
};

KingTurn kingTarget(bot::World const& w, int heardLen = 0) {
    KingTurn t;
    bot::Belief belief;
    belief.init(w);
    if (heardLen > 0) {
        bot::Record r;
        r.king = true;
        r.round = w.round - P.kingReportAge - 5;
        r.where = w.tileOf(4, 24);
        r.length = heardLen;
        r.id = 9;
        r.teamB = w.teamB;
        belief.mergeKing(r);
    }
    t.safety.prepare(w);
    t.targets.update(w);
    bot::DistField field;
    t.sought = bot::seekFeeder(w, t.safety, t.targets, field, bot::kingSeeksInFold(w, belief));
    return t;
}

void kingApproachesAFeeder() {
    auto const w = kingScene();
    auto turn = kingTarget(w);
    test::require(turn.sought && turn.targets.pearl == w.tileOf(20, 13),
                  "in the fold a king with no pearl nearby heads for the short teammate that would merge into it");
    int const dest = bot::bestMove(w, turn.safety, turn.targets).dest;
    test::require(w.torusDist(dest, w.tileOf(20, 13)) < w.torusDist(w.head, w.tileOf(20, 13)),
                  "the king's move closes on the feeder");

    auto const cascade = kingScene(P.mergeRound);
    bot::Safety safety;
    safety.prepare(cascade);
    bot::Targets targets;
    targets.update(cascade);
    bot::DistField field;
    test::require(bot::seekFeeder(cascade, safety, targets, field) && targets.pearl == cascade.tileOf(20, 13),
                  "from the merge round the protected king seeks its feeder without the fold's ask");
}

void kingKeepsNormalTargetsOtherwise() {
    auto pearl = kingScene();
    test::pearl(pearl, pearl.tileOf(22, 8));
    auto const turn = kingTarget(pearl);
    test::require(!turn.sought && turn.targets.pearl == pearl.tileOf(22, 8),
                  "a pearl two steps away beats approaching a teammate");

    auto enemy = kingScene();
    test::dragon(enemy, 3, true, enemy.tileOf(23, 11), 2);
    test::require(!kingTarget(enemy).sought, "with an enemy in view nobody merges, so the king plays normally");

    test::require(!kingTarget(kingScene(bot::foldStart() - 1)).sought, "before the fold the king plays normally");

    auto subject = kingScene();
    test::dragon(subject, 3, false, subject.tileOf(14, 12), 30);
    test::require(!kingTarget(subject).sought, "a dragon with a longer teammate in view is no king and seeks no feeder");

    auto const heard = kingScene();
    test::require(!kingTarget(heard, 17).sought,
                  "a fold king that once heard of a longer teammate, its report since aged out, seeks no feeder");
    test::require(kingTarget(heard, 16).sought, "a teammate heard of at our own length doesn't stop the fold king");

    auto twin = kingScene(P.mergeRound);
    twin.myId = 5;
    for (int i = 0; i < 16; ++i)
        twin.occId[twin.tileOf(20 - i, 8)] = 5;
    twin.dragons.front().id = 5;
    twin.dragons.back().visibleLen = 16;
    test::require(!kingTarget(twin).sought, "a length-16 teammate of lower id would not merge into our length-16 king");
}

int feedFor(bot::World const& w) {
    bot::DistField field;
    return bot::feedDirection(w, field);
}

// True when stepping `dir` kills us in place: kelp or our own body.
bool diesAlone(bot::World const& w, int dir) {
    int const to = w.step(w.head, dir);
    return to == bot::KELP || (to >= 0 && w.occId[to] == w.myId && !w.occHead[to]);
}

void equalTwinsMergeByLowerId() {
    auto const w = mergeScene();
    int const dir = feedFor(w);
    test::require(dir >= 0 && diesAlone(w, dir),
                  "in the merge phase a length-3 dragon dies beside a length-3 teammate of lower id two steps away");

    auto const twin = mergeScene(3, 3, 2, 5);
    test::require(feedFor(twin) < 0, "the lower-id twin in the same scene plays on");

    auto walled = mergeScene();
    test::wall(walled, walled.head, 0);
    int const walledDir = feedFor(walled);
    test::require(walledDir >= 0 && diesAlone(walled, walledDir), "kelp is an equally good way to die");
}

void survivorTargetsTheDrop() {
    // The lower-id twin at (10,8) after the id-5 dragon died at (8,8)..(6,8): its pearls lie at (8,8) and (6,8).
    auto w = test::world();
    w.myId = 2;
    test::body(w, {w.tileOf(10, 8), w.tileOf(11, 8), w.tileOf(12, 8)});
    w.round = P.mergeRound + 21;
    w.unitCount = 29;
    test::pearl(w, w.tileOf(8, 8));
    test::pearl(w, w.tileOf(6, 8));
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    targets.update(w);
    test::require(targets.pearl == w.tileOf(8, 8), "the surviving teammate targets the dropped pearl");
    test::require(bot::bestMove(w, safety, targets).dest == w.tileOf(9, 8), "and steps toward it");
}

void longerDragonsMergeIntoLongerOnly() {
    int const dir = feedFor(mergeScene(4, 9, 2, 5));
    test::require(dir >= 0, "a longer teammate takes our pearls whatever the ids");
    test::require(feedFor(mergeScene(4, 2)) < 0, "a teammate that looks shorter never makes us die");
    test::require(feedFor(mergeScene(9, 30)) >= 0, "no king minimum or length cap: a length-9 dragon merges too");
}

void playsNormallyOutsideTheMergePhase() {
    auto early = mergeScene();
    early.round = P.mergeRound - 1;
    test::require(feedFor(early) < 0, "before the merge round nothing merges");

    auto few = mergeScene();
    few.unitCount = P.mergeMinUnits;
    test::require(feedFor(few) < 0, "with the team down to the merge minimum nothing merges");
    few.unitCount = P.mergeMinUnits + 1;
    test::require(feedFor(few) >= 0, "one more dragon still merges");

    auto enemy = mergeScene();
    test::dragon(enemy, 7, true, enemy.tileOf(8, 11), 2);
    test::require(feedFor(enemy) < 0, "an enemy head in view could take the drop or needs us alive");
}

void mateMustBeFirstToOurHead() {
    auto rival = mergeScene();
    test::dragon(rival, 3, false, rival.tileOf(8, 9), 2);
    test::require(feedFor(rival) < 0, "a shorter teammate one step away would eat the pearls first");

    auto tie = mergeScene();
    test::dragon(tie, 3, false, tie.tileOf(8, 6), 2);
    test::require(feedFor(tie) < 0, "a tie with another head is not strictly first");

    auto const far = mergeScene(3, 3, 5, 2, 11);
    test::require(feedFor(far) < 0, "a teammate three steps away is beyond the merge radius");
}

void neverDiesIntoATeammateHead() {
    auto const w = mergeScene(3, 3, 5, 2, 9);
    int const dir = feedFor(w);
    test::require(dir >= 0 && diesAlone(w, dir) && w.step(w.head, dir) != w.tileOf(9, 8),
                  "with the teammate adjacent we die into our own body, never into its head");
}

} // namespace

int main() {
    return test::suite({
        {"equal twins merge by lower id", equalTwinsMergeByLowerId},
        {"survivor targets the drop", survivorTargetsTheDrop},
        {"longer dragons merge into longer only", longerDragonsMergeIntoLongerOnly},
        {"plays normally outside the merge phase", playsNormallyOutsideTheMergePhase},
        {"mate must be first to our head", mateMustBeFirstToOurHead},
        {"never dies into a teammate head", neverDiesIntoATeammateHead},
        {"king approaches a feeder", kingApproachesAFeeder},
        {"king keeps normal targets otherwise", kingKeepsNormalTargetsOtherwise},
    });
}
