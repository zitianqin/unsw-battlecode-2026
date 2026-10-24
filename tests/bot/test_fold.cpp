#include "support.hpp"
#include "fold.hpp"
#include "gossip.hpp"
#include "scoring.hpp"
#include "split.hpp"

namespace {

bot::Record kingRecord(int round, int where, int length, int id, bool teamB = false, bool heir = false) {
    bot::Record r;
    r.heir = heir;
    r.king = true;
    r.round = round;
    r.where = where;
    r.length = length;
    r.id = id;
    r.teamB = teamB;
    return r;
}

// A 32x32 board (or width x height) at `round`, 30 units: our dragon (id 5) of `length` with its head at (20,8)
// and its body trailing west, no other dragon in view, and a belief map that heard king `kingId` (length `kingLen`)
// at (kx, ky) `age` rounds ago.
struct FoldScene {
    bot::World w;
    bot::Belief b;
};

FoldScene foldScene(int round, int length = 20, int kingLen = 40, int kx = 20, int ky = 20, int age = 2,
                    int kingId = 2, int width = 32, int height = 32) {
    FoldScene s{test::world(width, height), {}};
    auto& w = s.w;
    w.myId = 5;
    std::vector<int> tiles;
    for (int i = 0; i < length; ++i)
        tiles.push_back(w.tileOf((20 - i + width) % width, 8));
    test::body(w, tiles);
    w.round = round;
    w.unitCount = 30;
    s.b.init(w);
    if (kingLen > 0)
        s.b.merge(kingRecord(round - age, w.tileOf(kx, ky), kingLen, kingId), round);
    bot::startFold(w);
    return s;
}

struct FoldTurn {
    bot::Fold fold;
    int target;
    int dest;
};

FoldTurn foldTurn(FoldScene& s) {
    bot::Safety safety;
    safety.prepare(s.w);
    bot::Targets targets;
    targets.update(s.w);
    bot::DistField field;
    auto const fold = bot::foldTowardKing(s.w, s.b, targets, field);
    return {fold, targets.pearl, bot::bestMove(s.w, safety, targets).dest};
}

void kingReportRoundTrips() {
    for (bool teamB : {false, true}) {
        auto const r = kingRecord(437, 4095, 511, 40000, teamB);
        auto const back = bot::decodeRecord(bot::encodeRecord(r, test::wire(teamB)), test::wire(teamB));
        test::require(back && back->king && !back->countdown && back->round == 437 && back->where == 4095 &&
                          back->length == 511 && back->id == 40000 && back->teamB == teamB,
                      "a king report survives the wire for both teams");
        test::require(!bot::decodeRecord(bot::encodeRecord(r, test::wire(teamB)) ^ (1ULL << 40), test::wire(teamB)), "a flipped id bit fails the tag");
        test::require(!back->heir, "an ordinary report is no heir's");
        auto const heir =
            bot::decodeRecord(bot::encodeRecord(kingRecord(437, 4095, 511, 40000, teamB, true), test::wire(teamB)), test::wire(teamB));
        test::require(heir && heir->king && heir->heir && heir->id == 40000 && heir->teamB == teamB,
                      "an heir's report keeps its heir bit and fields");
    }
    bot::Record c;
    c.countdown = true;
    c.round = 12;
    c.where = 300;
    c.wait = 7;
    auto const cd = bot::decodeRecord(bot::encodeRecord(c, test::wire(false)), test::wire(false));
    test::require(cd && cd->countdown && !cd->king && cd->where == 300 && cd->wait == 7,
                  "countdowns still decode as countdowns");
}

void beliefKeepsFreshOwnTeamKings() {
    auto w = test::world(32, 32);
    w.round = 440;
    bot::Belief b;
    b.init(w);
    test::require(!b.merge(kingRecord(438, 10, 40, 2, true), 440), "the other team's king report is dropped");
    test::require(b.merge(kingRecord(436, 10, 40, 2), 440) && b.merge(kingRecord(438, 12, 41, 2), 440),
                  "a fresher report of the same king replaces the older one");
    test::require(!b.merge(kingRecord(437, 14, 41, 2), 440), "an older report of it is not news");
    b.merge(kingRecord(439, 20, 30, 3), 440);
    auto const* best = b.bestKing(440, P.kingReportAge, -1);
    test::require(best && best->rec.id == 2 && best->rec.where == 12, "the longest fresh king wins");
    test::require(!b.bestKing(438 + P.kingReportAge + 1, P.kingReportAge, 3),
                  "a report older than kingReportAge is ignored");
    test::require(b.bestKing(440, P.kingReportAge, 2)->rec.id == 3, "we never pick ourselves when excluded");
    b.merge(kingRecord(435, 30, 41, 1), 440);
    test::require(b.bestKing(440, P.kingReportAge, -1)->rec.id == 1,
                  "equally long kings go to the lower id, however stale, so relays agree on one");
}

void kingReportIsGossipedFirstAndRelayedOnce() {
    auto w = test::world(32, 32);
    w.round = 440;
    bot::Belief b;
    b.init(w);
    for (int x : {3, 9, 15}) { // fresh views of pearls to compete with
        test::pearl(w, w.tileOf(x, 3));
        w.touched.push_back(w.tileOf(x, 3));
    }
    b.observe(w);
    test::require(!bot::Gossip::gather(w, b).empty(), "the scene has views to send");
    b.merge(kingRecord(439, 10, 40, 2), 440);
    auto records = bot::Gossip::gather(w, b);
    test::require(!records.empty() && records.front().king && records.front().round == 439,
                  "a heard king report leads the gossip");
    b.markKingSent(records.front());
    records = bot::Gossip::gather(w, b);
    test::require(records.empty() || !records.front().king, "once relayed it waits for a fresher report");
    b.merge(kingRecord(440, 11, 40, 2), 440);
    records = bot::Gossip::gather(w, b);
    test::require(!records.empty() && records.front().king && records.front().round == 440,
                  "a fresher report goes out again");
}

void onlyTheKingReportsItself() {
    auto s = foldScene(P.beaconRound, 30, 0);
    bot::reportKing(s.w, s.b);
    auto const* k = s.b.bestKing(s.w.round, P.kingReportAge, -1);
    test::require(k && k->rec.id == 5 && k->rec.length == 30 && k->rec.where == s.w.head,
                  "a length-30 dragon with no longer teammate reports its head and length");

    auto early = foldScene(bot::beaconStart() - 1, 30, 0);
    bot::reportKing(early.w, early.b);
    test::require(!early.b.bestKing(early.w.round, P.kingReportAge, -1), "no beacon before beaconStart on a map under kingSeedArea with no stalled unit count");

    auto heard = foldScene(P.beaconRound, 20, 40);
    bot::reportKing(heard.w, heard.b);
    test::require(heard.b.bestKing(heard.w.round, P.kingReportAge, 2) == nullptr,
                  "a dragon that heard of a longer king does not report itself");

    auto small = foldScene(P.beaconRound, 5, 0);
    bot::reportKing(small.w, small.b);
    test::require(small.b.bestKing(small.w.round, P.kingReportAge, -1),
                  "a length-5 dragon with no longer teammate reports itself");
    auto tiny = foldScene(P.beaconRound, P.kingMinLength - 1, 0);
    bot::reportKing(tiny.w, tiny.b);
    test::require(!tiny.b.bestKing(tiny.w.round, P.kingReportAge, -1), "one shorter than kingMinLength does not");

    // Only a king holds splits, and on a map under kingSeedArea (its unit count not stalled: fold.hpp's unitStall) there
    // is no king before beaconStart, so the hold starts at the fold's start.
    int const holdRound = bot::foldStart();
    auto holder = foldScene(holdRound, 30, 0);
    test::require(bot::kingHoldsSplits(holder.w, holder.b) == P.kingHoldsSplits,
                  "from the fold's start the king stops ordinary splits (with kingHoldsSplits)");
    auto before = foldScene(holdRound - 1, 30, 0);
    test::require(!bot::kingHoldsSplits(before.w, before.b), "before either it still splits");
    auto subject = foldScene(holdRound, 20, 40);
    test::require(!bot::kingHoldsSplits(subject.w, subject.b), "a dragon that heard of a longer king still splits");

    auto seen = foldScene(P.beaconRound, 20, 0);
    test::dragon(seen.w, 3, false, seen.w.tileOf(20, 12), 20);
    bot::reportKing(seen.w, seen.b);
    test::require(!seen.b.bestKing(seen.w.round, P.kingReportAge, -1),
                  "nor does one beside a visible teammate as long with a lower id");
}

void longDragonWalksToTheKing() {
    auto s = foldScene(440);
    auto const turn = foldTurn(s);
    int const king = s.w.tileOf(20, 20);
    test::require(turn.fold.active && turn.fold.kingId == 2 && turn.fold.kingLen == 40,
                  "a length-20 dragon hearing a length-40 king 12 tiles away at r440 folds");
    test::require(turn.target == king, "and targets the king's reported head");
    test::require(s.w.torusDist(turn.dest, king) < s.w.torusDist(s.w.head, king), "its move closes on the king");

    auto seen = foldScene(440);
    test::dragon(seen.w, 2, false, seen.w.tileOf(24, 12), 9);
    auto const seenTurn = foldTurn(seen);
    test::require(seenTurn.fold.active && seenTurn.target == seen.w.tileOf(24, 12),
                  "a king in view is followed where it stands, whatever its visible length");
}

// A fight (Targets::addFights) 3 steps ahead, an enemy head with a teammate's head beside it, is no pearl for the
// fold's P.kingPearlReach exception: the folder still targets the king.
void folderPassesAFightBy() {
    auto s = foldScene(440);
    test::dragon(s.w, 9, true, s.w.tileOf(23, 8), 6);
    test::dragon(s.w, 7, false, s.w.tileOf(24, 10), 3);
    bot::Targets targets;
    targets.update(s.w);
    test::require(targets.pearl == s.w.tileOf(23, 8) || !P.harvestFights, "the scenario: the fight is the target");
    auto const turn = foldTurn(s);
    test::require(turn.fold.active && turn.target == s.w.tileOf(20, 20), "the folder targets the king, not the fight");
}

void shortDragonFoldsFromTheStart() {
    // Just after foldStart a length-3 dragon at (20,8) hears of a length-5 king at (20,18), 10 steps south.
    auto s = foldScene(bot::foldStart() + 5, 3, 5, 20, 18);
    auto const turn = foldTurn(s);
    int const king = s.w.tileOf(20, 18);
    test::require(turn.fold.active && turn.fold.kingId == 2 && turn.fold.kingLen == 5,
                  "a length-3 dragon hearing of a length-5 king 10 steps away folds");
    test::require(turn.target == king && s.w.torusDist(turn.dest, king) < s.w.torusDist(s.w.head, king),
                  "and walks towards it");

    auto beside = foldScene(bot::foldStart() + 5, 3, 5, 20, 18);
    test::dragon(beside.w, 2, false, beside.w.tileOf(20, 10), 5);
    bot::DistField field;
    auto const fold = foldTurn(beside).fold;
    int const dir = bot::feedDirection(beside.w, field, fold);
    test::require(fold.active && dir >= 0, "two steps from the king it merges into it");

    // A longer teammate that isn't the king, two steps away, doesn't take a fold's merge before the cascade.
    auto passing = foldScene(bot::foldStart() + 5, 3, 5, 20, 18);
    test::dragon(passing.w, 7, false, passing.w.tileOf(22, 8), 6);
    auto const passFold = foldTurn(passing).fold;
    test::require(passFold.active && (bot::feedDirection(passing.w, field, passFold) >= 0) == !P.foldMergesOnlyKing,
                  "on its way to the king a folder merges into no other teammate (with foldMergesOnlyKing)");
    passing.w.round = P.mergeRound;
    test::require(bot::feedDirection(passing.w, field, passFold) >= 0,
                  "from mergeRound the cascade merges it into that teammate as before");

    auto early = foldScene(bot::foldStart() - 1, 3, 5, 20, 18);
    test::require(!foldTurn(early).fold.active, "before foldStart it keeps playing");

    // At r480 a length-3 dragon has 500 - 1 - 480 - 3 = 16 rounds to reach the king and be eaten: shorter than
    // P.foldMinFeedRounds, it keeps the whole-length rule.
    test::require(P.foldMinFeedRounds >= 3, "the scene assumes a feed time of at least a length-3 dragon's");
    auto inTime = foldScene(480, 3, 5, 28, 16);
    test::require(foldTurn(inTime).fold.active, "a king 16 steps away is reached in time");
    auto tooFar = foldScene(480, 3, 5, 29, 16);
    test::require(!foldTurn(tooFar).fold.active, "a king 17 steps away is not: it keeps eating");
}

void foldBoundaries() {
    auto late = foldScene(490, 20, 40, 20, 23);
    test::require(!foldTurn(late).fold.active, "at r490 a length-20 dragon 15 tiles away keeps eating");
    // It needs its 15 steps plus its feed time: its length, or with partialFold at most foldMinFeedRounds.
    int const lastFold = unswbc::Constants::MAX_ROUNDS - 1 - 15 - (P.partialFold ? std::min(20, P.foldMinFeedRounds) : 20);
    auto inTime = foldScene(lastFold, 20, 40, 20, 23);
    test::require(foldTurn(inTime).fold.active, "at the last round with 15 steps and its feed time to spare it folds");
    auto tooLate = foldScene(lastFold + 1, 20, 40, 20, 23);
    test::require(!foldTurn(tooLate).fold.active, "one round later it keeps eating");

    auto stale = foldScene(440, 20, 40, 20, 20, 20);
    test::require(!foldTurn(stale).fold.active, "a report 20 rounds old 12 steps off is ignored");
    // With P.reportByDrift a folder 24 steps off (48x48, (20,8) to (20,32)) trusts a report while the king can have
    // moved no farther than that path (age up to P.driftFactor x 24), and takes it over a fresh shorter king's report
    // as bestKing ranks them; one round older it falls back to the fresh one.
    int const trusted = static_cast<int>(P.driftFactor * 24);
    auto far = foldScene(440, 20, 40, 20, 32, trusted, 2, 48, 48);
    far.b.merge(kingRecord(438, far.w.tileOf(26, 8), 30, 3), 440);
    test::require(foldTurn(far).fold.kingId == (P.reportByDrift || trusted <= P.kingReportAge ? 2 : 3),
                  "an old longer report the king's drift can't outrun sends a far folder to it");
    auto farStale = foldScene(440, 20, 40, 20, 32, std::max(trusted, P.kingReportAge) + 1, 2, 48, 48);
    farStale.b.merge(kingRecord(438, farStale.w.tileOf(26, 8), 30, 3), 440);
    test::require(foldTurn(farStale).fold.kingId == 3, "one round older it walks to the fresh one");
    // At the round that leaves the 20-long folder 19 rounds of path after its feed time (lastFold - 4), an old report
    // 24 steps off is out of reach, so it doesn't stop the fold to the fresh one within reach.
    int const reachRound = lastFold - 4;
    auto outOfReach = foldScene(reachRound, 20, 40, 20, 32, P.kingReportAge + 1, 2, 48, 48);
    outOfReach.b.merge(kingRecord(reachRound - 2, outOfReach.w.tileOf(26, 8), 30, 3), reachRound);
    test::require(foldTurn(outOfReach).fold.kingId == 3, "an old report beyond the fold's reach leaves the fresh one");
    foldScene(440); // the 48x48 scenes above set the fold's start from their board: back to this 32x32 one's
    auto early = foldScene(bot::foldStart() - 1);
    test::require(!foldTurn(early).fold.active, "before foldStart nothing changes");
    // At the unit floor a 5-long folder 3 steps from a 12-long king walks to it only once the rest of the team
    // outnumbers what the fastest wipe-out for the map's area could kill in the rounds left (P.floorByTime).
    auto big = foldScene(485, 5, 12, 23, 8, 2, 2, 64, 64);
    big.w.unitCount = 6;
    test::require(foldTurn(big).fold.active == (P.foldIgnoresMinUnits || P.floorByTime),
                  "64x64 at r485, team of 6: the other 5 outlast a wipe-out, so it folds past the floor");
    auto small = foldScene(485, 5, 12, 23, 8, 2, 2);
    small.w.unitCount = 6;
    test::require(foldTurn(small).fold.active == P.foldIgnoresMinUnits,
                  "32x32 at r485, team of 6: heads are denser, the rest could still be wiped out: it keeps the floor");
    auto last = foldScene(490, 5, 12, 23, 8, 2, 2);
    last.w.unitCount = 7;
    test::require(foldTurn(last).fold.active == (P.foldIgnoresMinUnits || P.floorByTime),
                  "32x32 at r490, team of 7: too few rounds left to wipe out the other 6, so it folds");
    small.w.unitCount = P.mergeMinUnits + 1;
    test::require(foldTurn(small).fold.active, "above the floor it folds as before");
    auto king = foldScene(440, 30, 30, 20, 20, 2, 7);
    test::require(!foldTurn(king).fold.active, "a report of an equal king with a higher id does not fold us");
    auto self = foldScene(440, 20, 40, 20, 20, 2, 5);
    test::require(!foldTurn(self).fold.active, "the king never folds into its own report");

    auto pearl = foldScene(440);
    test::pearl(pearl.w, pearl.w.tileOf(22, 8));
    auto const pearlTurn = foldTurn(pearl);
    test::require(pearlTurn.fold.active && pearlTurn.target == pearl.w.tileOf(22, 8),
                  "a pearl within kingPearlReach is eaten on the way");
}

void folderMergesIntoTheReportedKing() {
    // Our length-20 dragon at (20,8), the king's head two steps east with only 8 segments in view.
    auto s = foldScene(P.mergeRound);
    test::dragon(s.w, 2, false, s.w.tileOf(22, 8), 8);
    bot::DistField field;
    test::require(bot::feedDirection(s.w, field) < 0,
                  "outside the fold the king's short visible chain does not look long enough");
    bot::Fold const fold{true, 2, 40};
    int const dir = bot::feedDirection(s.w, field, fold);
    int const to = s.w.step(s.w.head, dir);
    test::require(dir >= 0 && to >= 0 && s.w.occId[to] == s.w.myId,
                  "the folding dragon dies into its own body beside the king it heard is 40 long");

    s.w.unitCount = P.mergeMinUnits;
    s.w.round = unswbc::Constants::MAX_ROUNDS - 5;
    test::require((bot::feedDirection(s.w, field, fold) >= 0) == (P.foldIgnoresMinUnits || P.floorByTime),
                  "4 rounds from the end nobody can wipe out the other 7: a fold merges past the unit floor");
    s.w.round = bot::foldStart();
    test::require((bot::feedDirection(s.w, field, fold) >= 0) == P.foldIgnoresMinUnits,
                  "at the fold's start on 32x32 the rest could still be wiped out: the unit floor holds");
    auto wide = foldScene(P.mergeRound);
    test::dragon(wide.w, 2, false, wide.w.tileOf(20 + P.foldMergeRadius, 8), 40);
    test::require(bot::feedDirection(wide.w, field) < 0, "the merge cascade waits for a teammate within mergeRadius");
    test::require(bot::feedDirection(wide.w, field, fold) >= 0, "a folding dragon merges within foldMergeRadius");
    auto beyond = foldScene(P.mergeRound);
    test::dragon(beyond.w, 2, false, beyond.w.tileOf(21 + P.foldMergeRadius, 8), 40);
    test::require(bot::feedDirection(beyond.w, field, fold) < 0, "but not one step further");

    auto near = foldScene(P.mergeRound);
    test::dragon(near.w, 2, false, near.w.tileOf(22, 8), 8);
    test::dragon(near.w, 9, true, near.w.tileOf(20, 9), 3);
    test::require(bot::feedDirection(near.w, field, fold) < 0, "never with an enemy head closer than the king");
}

// P.foldFeedByDrop: an enemy head in view holds a folder's merge unless the king reaches more of the drop (our head
// and every second segment) first than the enemy does.
void folderRacesTheEnemyForItsDrop() {
    bot::DistField field;
    bot::Fold const fold{true, 2, 40};
    // A length-5 folder at (20,8)..(16,8), the king's head two steps east: drops at (20,8), (18,8), (16,8).
    auto const scene = [](int ex, int ey, int enemyLen) {
        auto s = foldScene(P.foldRound, 5);
        test::dragon(s.w, 2, false, s.w.tileOf(22, 8), 8);
        test::dragon(s.w, 9, true, s.w.tileOf(ex, ey), enemyLen);
        return s;
    };
    auto far = scene(25, 8, 3);
    test::require((bot::feedDirection(far.w, field, fold) >= 0) == (P.foldIgnoresEnemy || P.foldFeedByDrop),
                  "an enemy head three steps beyond the king reaches no drop first: the folder merges");
    auto tail = scene(16, 9, 2);
    test::require((bot::feedDirection(tail.w, field, fold) >= 0) == P.foldIgnoresEnemy,
                  "a short enemy head beside our tail eats the tail-end drops first: the folder holds");

    // Boundary: a length-3 folder at (20,8)..(18,8), the king at (19,9), two steps from both drops, an enemy head three
    // steps east. Sprinting 3 steps it is on our head's drop in the round the king steps beside it; 2 steps and a step
    // on, it ties there, and the tail's drop is the king's.
    auto const east = [](int enemyLen) {
        auto s = foldScene(P.foldRound, 3);
        test::dragon(s.w, 2, false, s.w.tileOf(19, 9), 8);
        test::dragon(s.w, 9, true, s.w.tileOf(23, 8), enemyLen);
        return s;
    };
    auto slow = east(3), sprinter = east(4);
    test::require((bot::feedDirection(slow.w, field, fold) >= 0) == (P.foldIgnoresEnemy || P.foldFeedByDrop),
                  "an enemy that can't sprint onto a drop before the king ties at best: the folder merges");
    test::require((bot::feedDirection(sprinter.w, field, fold) >= 0) == P.foldIgnoresEnemy,
                  "one that can pay the sprint onto our head's drop counts as first there: the folder holds");
}

// Long dragons too far from the king to be eaten whole still fold with P.partialFold, when the king has
// P.foldMinFeedRounds rounds to eat part of the drop; on 70x30 boards (below localFoldArea) no foldReach applies.
void partialFoldBoundaries() {
    auto scene = [](int round, int length, int kingLen, int kx, int ky) {
        auto s = foldScene(round, length, kingLen, kx, ky, 2, 2, 70, 30);
        test::require(s.w.N < P.localFoldArea, "the scene is below localFoldArea");
        return s;
    };
    auto folds = [&](int round, int length, int kingLen, int kx, int ky) {
        auto s = scene(round, length, kingLen, kx, ky);
        return foldTurn(s).fold.active;
    };
    // A length-60 dragon at (20,8), a length-70 king at (30,18): 20 steps. At r440, 59 rounds are left after this
    // one: too few for 20 steps and 60 of eating, enough for 20 and foldMinFeedRounds.
    test::require(P.foldMinFeedRounds <= 24, "the scenes assume a feed time well below the dragons' lengths");
    auto big = scene(440, 60, 70, 30, 18);
    auto const bigTurn = foldTurn(big);
    test::require(bigTurn.fold.active == P.partialFold, "at r440 a length-60 dragon 20 steps from the king folds");
    if (P.partialFold) {
        test::require(bigTurn.target == big.w.tileOf(30, 18), "and walks to the king");
        int const last = unswbc::Constants::MAX_ROUNDS - 1 - 20 - P.foldMinFeedRounds;
        test::require(folds(last, 60, 70, 30, 18), "up to the round that leaves the feed time");
        test::require(!folds(last + 1, 60, 70, 30, 18), "one round later it keeps eating");
    }
    auto whole = scene(unswbc::Constants::MAX_ROUNDS - 1 - 20 - 60, 60, 70, 30, 18);
    test::require(foldTurn(whole).fold.active, "with time to be eaten whole it folds either way");

    // A length-30 dragon with a fresh report of a king 45 steps away (35 east, 10 south): at r430 69 rounds are
    // left, 45 + 30 too few to be eaten whole.
    test::require(folds(430, 30, 40, 55, 18) == P.partialFold,
                  "at r430 a length-30 dragon 45 steps from the king folds");
    test::require(!folds(490, 30, 40, 40, 8),
                  "at r490 with the king 20 steps away there is no time to eat anything: it keeps eating");
    test::require(!folds(bot::foldStart() - 1, 60, 70, 30, 18), "nothing before the fold starts");
}

unswbc::Controller controllerFor(bot::World const& w) {
    unswbc::Controller controller(w.myId, unswbc::Team::A, unswbc::Direction::EAST, {}, w.unitLimit);
    controller.length = w.length;
    controller.unit_count = w.unitCount;
    controller.head.position = {w.xOf(w.head), w.yOf(w.head)};
    return controller;
}

// The child length shouldSplit would give on the scene's turn, as main asks it when the dragon doesn't fold.
int splitChild(FoldScene& s) {
    bot::Safety safety;
    safety.prepare(s.w);
    bot::Targets targets;
    targets.update(s.w);
    return bot::shouldSplit(s.w, controllerFor(s.w), safety, bot::bestMove(s.w, safety, targets));
}

void bigMapsFoldOnlyNearTheKing() {
    // 64x64 at r420: a length-6 dragon at (20,8) hears of a length-30 king straight south of it.
    auto near = foldScene(420, 6, 30, 20, 8 + P.foldReach, 2, 2, 64, 64);
    auto const nearTurn = foldTurn(near);
    test::require(nearTurn.fold.active && nearTurn.target == near.w.tileOf(20, 8 + P.foldReach),
                  "a dragon foldReach steps from the king folds and walks to it");
    auto edge = foldScene(420, 6, 30, 20, 9 + P.foldReach, 2, 2, 64, 64);
    test::require(foldTurn(edge).fold.active == !P.localFold, "one step further it doesn't (with localFold)");

    auto far = foldScene(420, 6, 30, 20, 38, 2, 2, 64, 64);
    auto const farTurn = foldTurn(far);
    test::require(farTurn.fold.active == !P.localFold, "a dragon 30 steps from the king keeps playing (with localFold)");
    if (P.localFold) {
        test::require(farTurn.target != far.w.tileOf(20, 38), "it doesn't target the king");
        test::require(!bot::kingHoldsSplits(far.w, far.b), "the far donor isn't mistaken for a king");
        test::require(splitChild(far) == P.childLength, "and keeps splitting");
        far.w.unitCount = far.w.unitLimit - bot::rescueReserve(far.w);
        test::require(splitChild(far) == 0, "the usual split ceiling still applies");
        --far.w.unitCount;
        test::require(splitChild(far) == P.childLength, "a feeder's freed slot can be refilled");
    }
    auto late = foldScene(unswbc::Constants::MAX_ROUNDS - 1 - 6 - P.foldReach,
                          6, 30, 20, 8 + P.foldReach, 0, 2, 64, 64);
    test::require(foldTurn(late).fold.active, "at the reach limit it still folds with exactly enough time");
    ++late.w.round;
    test::require(!foldTurn(late).fold.active, "near the king it still needs time to be eaten");

    auto compact = foldScene(420, 6, 30, 3, 23);
    test::require(foldTurn(compact).fold.active, "a king 30 path steps away on 32x32 still draws a fold");

    // Below localFoldArea tiles every dragon with time to reach the king folds, as before. On 48 wide boards the
    // king is 15 east and 15 south: 30 steps.
    int const high = (P.localFoldArea + 47) / 48;
    auto big = foldScene(420, 6, 30, 35, 23, 2, 2, 48, high);
    test::require(big.w.N >= P.localFoldArea && foldTurn(big).fold.active == !P.localFold,
                  "on a board of localFoldArea tiles a king 30 steps away draws no fold (with localFold)");
    auto small = foldScene(420, 6, 30, 35, 23, 2, 2, 48, high - 1);
    test::require(small.w.N < P.localFoldArea && foldTurn(small).fold.active,
                  "one row smaller the dragon 30 steps away folds as before");
}

void localFoldUsesPaths() {
    auto detour = foldScene(420, 6, 30, 20, 8 + P.foldReach, 2, 2, 64, 64);
    test::wall(detour.w, detour.w.tileOf(20, 10), 2);
    test::require(foldTurn(detour).fold.active == !P.localFold,
                  "a wall detour puts the king beyond foldReach despite its geometric distance");

    auto portal = foldScene(420, 6, 30, 20, 38, 2, 2, 64, 64);
    int const mouth = portal.w.tileOf(20, 9);
    int const landing = portal.w.tileOf(20, 37);
    portal.w.rememberEdge(portal.w.edgeOf(mouth, 2), unswbc::Edge(false, unswbc::EdgeType::PORTAL, 5));
    portal.w.rememberEdge(portal.w.edgeOf(landing, 0), unswbc::Edge(false, unswbc::EdgeType::PORTAL, 5));
    test::require(foldTurn(portal).fold.active, "a known portal brings a distant king within folding reach");
}

// A split-out child: our dragon (id 5) was born at `round` with `length` segments, head at (20,8), body trailing
// west; its parent (id 3) filed a king report `age` rounds before our birth with its head at (px, py), MIN_SIZE
// longer than we are.
FoldScene childScene(int round, int length, int px, int py, int age = 0, int parentLen = -1) {
    auto s = foldScene(round, length, 0);
    s.w.firstRound = round;
    s.w.bornLength = length;
    s.w.bornHead = s.w.head;
    int const len = parentLen > 0 ? parentLen : length + unswbc::Constants::MIN_SIZE;
    s.b.merge(kingRecord(round - age, s.w.tileOf(px, py), len, 3), round);
    return s;
}

void splitOutKingPassesTheCrown() {
    // r450: a king of 20 (id 3, head reported at (22,10), 4 tiles from our birth head) splits out; we are its 18.
    int const len = 18, parentLen = len + unswbc::Constants::MIN_SIZE;
    auto child = childScene(450, len, 22, 10);
    bot::reportKing(child.w, child.b);
    auto const* k = child.b.bestKing(450, P.kingReportAge, -1);
    test::require(child.b.heirOf == 3, "the child recognises its dying parent's report");
    test::require(k && k->rec.id == 5 && k->rec.heir && k->rec.length == len,
                  "and reports itself king, as heir, on its first turn");
    auto const again = bot::decodeRecord(bot::encodeRecord(k->rec, test::wire(false)), test::wire(false));
    test::require(again && again->heir, "its report goes out with the heir bit");
    child.w.round = 450 + P.kingReportAge;
    bot::reportKing(child.w, child.b);
    test::require(child.b.bestKing(child.w.round, P.kingReportAge, -1)->rec.heir,
                  "it stays the heir while the parent's last report is fresh");
    child.w.round++;
    bot::reportKing(child.w, child.b);
    test::require(!child.b.bestKing(child.w.round, P.kingReportAge, -1)->rec.heir, "and plain king after");

    // A length-6 folder (id 5) at (20,8) holds the parent's report and the heir's: the child is 6 tiles south.
    auto folder = foldScene(451, 6, parentLen, 20, 16, 2, 3);
    int const heirHead = folder.w.tileOf(20, 14);
    folder.b.merge(kingRecord(451, heirHead, len, 40, false, true), 451);
    auto const turn = foldTurn(folder);
    test::require(turn.fold.active && turn.fold.kingId == 40 && turn.fold.kingLen == len && turn.target == heirHead,
                  "a folder that heard both walks to the heir, not the dead parent");
    folder.b.merge(kingRecord(450, folder.w.tileOf(20, 17), parentLen, 3), 451);
    test::require(foldTurn(folder).fold.kingId == 40, "a later relay of the parent's last report changes nothing");
    test::dragon(folder.w, 40, false, folder.w.tileOf(20, 10), 5);
    bot::DistField field;
    auto const near = foldTurn(folder).fold;
    int const dir = bot::feedDirection(folder.w, field, near);
    int const to = folder.w.step(folder.w.head, dir);
    test::require(dir >= 0 && to >= 0 && folder.w.occId[to] == folder.w.myId,
                  "two steps from the heir it merges beside it");

    // A child that heard nothing of its parent is king and reports as heir just the same; a dragon born short (an
    // ordinary split's or a shed's child) or, on a map under kingSeedArea (where a seeding king's young child reads no
    // unit trend), before the beacon started isn't a king's split-out child.
    auto deaf = childScene(450, len, 22, 10);
    deaf.b.kings.clear();
    bot::reportKing(deaf.w, deaf.b);
    k = deaf.b.bestKing(450, P.kingReportAge, -1);
    test::require(deaf.b.heirOf < 0 && k && k->rec.id == 5 && k->rec.heir, "a child that heard nothing is heir too");
    auto born = childScene(450, len, 22, 10);
    born.b.kings.clear();
    born.w.bornLength = P.childLength;
    bot::reportKing(born.w, born.b);
    test::require(!born.b.bestKing(450, P.kingReportAge, -1)->rec.heir, "a dragon born childLength long is no heir");
    int const beacon = bot::beaconStart();
    auto early = childScene(beacon - 1, len, 22, 10);
    early.b.kings.clear();
    early.w.round = beacon;
    bot::reportKing(early.w, early.b);
    test::require(!early.b.bestKing(beacon, P.kingReportAge, -1)->rec.heir,
                  "nor is one born before beaconStart on a map under kingSeedArea, when no parent could be king");

    auto plain = foldScene(451, 6, parentLen, 20, 16, 2, 3);
    plain.b.merge(kingRecord(451, heirHead, len, 40), 451);
    test::require(foldTurn(plain).fold.kingId == 3,
                  "a new king's report without the heir bit leaves the longer report in place");
}

void successionBoundaries() {
    int const len = 18, reach = len + unswbc::Constants::MIN_SIZE - 1;
    // Our birth head is (20,8); the parent's report must be within its length less one of it (10 east, then south).
    auto edge = childScene(450, len, 30, 8 + reach - 10);
    bot::reportKing(edge.w, edge.b);
    test::require(edge.b.heirOf == 3, "a parent head as far as its tail can reach is our parent");
    auto beyond = childScene(450, len, 30, 9 + reach - 10);
    bot::reportKing(beyond.w, beyond.b);
    test::require(beyond.b.heirOf < 0 && beyond.b.bestKing(450, P.kingReportAge, -1)->rec.id == 3,
                  "one tile further it is another king of that length: we don't report ourselves");
    auto older = childScene(450, len, 30, 10 + reach - 10, 2);
    bot::reportKing(older.w, older.b);
    test::require(older.b.heirOf == 3, "a report two rounds old allows two more tiles the parent moved since");

    // A different king of our parent's length at a far tile is still followed.
    auto far = childScene(P.foldRound + 20, len, 4, 24);
    test::require(far.w.torusDist(far.w.tileOf(4, 24), far.w.head) > reach, "the scene's king is far");
    bot::reportKing(far.w, far.b);
    auto const farTurn = foldTurn(far);
    test::require(far.b.heirOf < 0 && farTurn.fold.active && farTurn.fold.kingId == 3,
                  "a child hearing a far king of its parent's length folds to it");

    // The child of a non-king split-out, with a genuinely longer king nearby, still folds to that king.
    auto longer = childScene(P.foldRound + 20, len, 22, 10, 0, len + unswbc::Constants::MIN_SIZE + 1);
    auto const longerTurn = foldTurn(longer);
    bot::reportKing(longer.w, longer.b);
    test::require(longer.b.heirOf < 0 && longerTurn.fold.active && longerTurn.fold.kingId == 3,
                  "a king longer than our parent could have been is followed");

    // A folder holding an heir's report (filed at r452) keeps a king that isn't its parent: too far, too long, a
    // later id, or reporting after the heir.
    auto folders = [&](int kx, int ky, int kLen, int kId, int kRound) {
        auto s = foldScene(453, 6, 0);
        s.b.merge(kingRecord(452, s.w.tileOf(20, 14), len, 40, false, true), 453);
        s.b.merge(kingRecord(kRound, s.w.tileOf(kx, ky), kLen, kId), 453);
        return foldTurn(s).fold.kingId;
    };
    // The heir's head is (20,14); a report a round older may be reach + 1 tiles from it (10 east, then south).
    test::require(folders(30, 14 + reach + 1 - 10, len + 2, 3, 451) == 40, "a parent report close enough is dropped");
    test::require(folders(30, 15 + reach + 1 - 10, len + 2, 3, 451) == 3, "one tile further it is kept");
    test::require(folders(20, 16, len + 3, 3, 451) == 3, "so is a king more than MIN_SIZE longer");
    test::require(folders(20, 16, len + 2, 41, 451) == 41, "so is a king with a later id than the heir");
    test::require(folders(20, 16, len + 2, 3, 452) == 40, "a parent report filed the round the heir reported is dropped");
    test::require(folders(20, 16, len + 2, 3, 453) == 3, "so a king reporting after the heir is kept");

    // A live king of our parent's length (the parent tied it) is taken for our parent until it reports again.
    auto tie = childScene(450, len, 22, 10);
    bot::reportKing(tie.w, tie.b);
    test::require(tie.b.heirOf == 3, "a king of the parent's length beside our birth passes for our parent");
    tie.w.round = 452;
    tie.b.merge(kingRecord(452, tie.w.tileOf(22, 12), len + 2, 3), 452);
    bot::reportKing(tie.w, tie.b);
    auto const tieTurn = foldTurn(tie);
    test::require(tieTurn.fold.active && tieTurn.fold.kingId == 3, "once it reports after our birth we fold to it");

    // Folders drop a report only if it was filed by the heir's earliest heir report, and bound its length by the
    // heir's length then, not after it has eaten.
    auto live = foldScene(454, 6, 0);
    live.b.merge(kingRecord(451, live.w.tileOf(20, 14), len, 40, false, true), 454);
    live.b.merge(kingRecord(453, live.w.tileOf(20, 15), len, 40, false, true), 454);
    live.b.merge(kingRecord(452, live.w.tileOf(20, 17), len + 1, 3), 454);
    test::require(foldTurn(live).fold.kingId == 3, "a live king reporting after the heir's birth stays king");
    auto grown = foldScene(453, 6, 0);
    grown.b.merge(kingRecord(451, grown.w.tileOf(20, 14), len, 40, false, true), 453);
    grown.b.merge(kingRecord(453, grown.w.tileOf(20, 15), len + 2, 40, false, true), 453);
    grown.b.merge(kingRecord(450, grown.w.tileOf(20, 17), len + 3, 3), 453);
    test::require(foldTurn(grown).fold.kingId == 3, "a king more than MIN_SIZE longer than the heir at birth is kept");
}

// With P.modeModel the fold window grows with the map (fold.hpp's foldWindowFor): a 32x32 board's fold starts before a
// 32x16 board's and before P.beaconRound, and there a lone long dragon beacons, and a dragon holding its report folds,
// from that round and not the round before; a 100x100 board's window stops at P.foldWindowMax.
void foldStartGrowsWithTheMap() {
    if (!P.modeModel)
        return;
    auto startOn = [](int width, int height) {
        foldScene(300, 30, 0, 20, 20, 2, 2, width, height); // sets the start from its board, as main does each turn
        return bot::foldStart();
    };
    int const smallStart = startOn(32, 16);
    int const huge = startOn(100, 100);
    int const start = startOn(32, 32);
    test::require(start < smallStart && smallStart <= unswbc::Constants::MAX_ROUNDS - P.foldWindow && start < P.beaconRound,
                  "the bigger board folds first, both no later than the fixed window, before the beacon round");
    test::require(huge == unswbc::Constants::MAX_ROUNDS - P.foldWindowMax, "a huge board's window stops at foldWindowMax");

    auto king = foldScene(start, 30, 0);
    bot::reportKing(king.w, king.b);
    test::require(king.b.bestKing(start, P.kingReportAge, -1), "a lone long dragon beacons from the fold's start");
    auto early = foldScene(start - 1, 30, 0);
    bot::reportKing(early.w, early.b);
    test::require(!early.b.bestKing(start - 1, P.kingReportAge, -1), "but not the round before");

    auto folder = foldScene(start, 3, 40, 20, 14);
    test::require(foldTurn(folder).fold.active, "a dragon holding a king report folds from the fold's start");
    auto waiting = foldScene(start - 1, 3, 40, 20, 14);
    test::require(!foldTurn(waiting).fold.active, "and plays on the round before");
}

} // namespace

int main() {
    return test::suite({
        {"a folder passes a fight by", folderPassesAFightBy},
        {"king report round-trips", kingReportRoundTrips},
        {"belief keeps fresh own-team kings", beliefKeepsFreshOwnTeamKings},
        {"king report is gossiped first and relayed once", kingReportIsGossipedFirstAndRelayedOnce},
        {"only the king reports itself", onlyTheKingReportsItself},
        {"long dragon walks to the king", longDragonWalksToTheKing},
        {"short dragon folds from the start", shortDragonFoldsFromTheStart},
        {"fold boundaries", foldBoundaries},
        {"partial fold boundaries", partialFoldBoundaries},
        {"big maps fold only near the king", bigMapsFoldOnlyNearTheKing},
        {"local fold uses paths", localFoldUsesPaths},
        {"folder merges into the reported king", folderMergesIntoTheReportedKing},
        {"folder races the enemy for its drop", folderRacesTheEnemyForItsDrop},
        {"split-out king passes the crown", splitOutKingPassesTheCrown},
        {"succession boundaries", successionBoundaries},
        {"fold start grows with the map", foldStartGrowsWithTheMap},
    });
}
