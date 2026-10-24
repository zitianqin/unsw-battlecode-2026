#include "support.hpp"
#include "capfeed.hpp"
#include "fold.hpp"
#include "gossip.hpp"
#include "scoring.hpp"
#include "split.hpp"

// Seeding the king (fold.hpp's kingSeedOn): on a map of at least P.kingSeedArea tiles the king reports itself before
// the beacon and holds its ordinary splits and farm trips near the split ceiling and from P.kingSeedRound; on any map
// it does so while the team's unit count has stalled (fold.hpp's unitStall); otherwise smaller maps play as before.

namespace {

bot::Record kingRecord(int round, int where, int length, int id) {
    bot::Record r;
    r.king = true;
    r.round = round;
    r.where = where;
    r.length = length;
    r.id = id;
    return r;
}

// A width x height board at `round` with `units` dragons on our team: our dragon (id 5) of `length`, head at (20,8)
// and its body trailing west, on open ground with nothing else in view.
struct Scene {
    bot::World w;
    bot::Belief b;
};

Scene scene(int round, int length = 10, int width = 64, int height = 64, int units = 30) {
    Scene s{test::world(width, height), {}};
    s.w.myId = 5;
    std::vector<int> tiles;
    for (int i = 0; i < length; ++i)
        tiles.push_back(s.w.tileOf((20 - i + width) % width, 8));
    test::body(s.w, tiles);
    s.w.round = round;
    s.w.unitCount = units;
    s.b.init(s.w);
    return s;
}

unswbc::Controller controllerFor(bot::World const& w) {
    unswbc::Controller controller(w.myId, unswbc::Team::A, unswbc::Direction::EAST, {}, w.unitLimit);
    controller.length = w.length;
    controller.unit_count = w.unitCount;
    controller.head.position = {w.xOf(w.head), w.yOf(w.head)};
    return controller;
}

// The child length shouldSplit gives on this turn, with main's hold for a seeding king.
int splitChild(Scene& s) {
    bot::Safety safety;
    safety.prepare(s.w);
    bot::Targets targets;
    targets.update(s.w);
    return bot::shouldSplit(s.w, controllerFor(s.w), safety, bot::bestMove(s.w, safety, targets), nullptr,
                            bot::kingSeedHolds(s.w, s.b));
}

// Where the ordinary split stops on this board: the unit cap less rescueReserve.
int ceiling(bot::World const& w) { return w.unitLimit - bot::rescueReserve(w); }

void kingHoldsFromTheSeedRound() {
    if (!P.kingSeedOnValue)
        return;
    auto s = scene(P.kingSeedRound);
    test::require(bot::kingSeedHolds(s.w, s.b) && bot::kingSkipsFarms(s.w, s.b),
                  "on a 64 x 64 board from kingSeedRound the longest dragon holds its splits and farm trips");
    test::require(splitChild(s) == 0, "so it makes no ordinary split");
    bot::reportKing(s.w, s.b);
    auto const* k = s.b.bestKing(s.w.round, P.kingReportAge, -1);
    test::require(k && k->rec.id == 5 && s.w.round < P.beaconRound, "and reports itself before the beacon");

    auto before = scene(P.kingSeedRound - 1);
    test::require(before.w.unitCount < ceiling(before.w) - P.kingHoldLead && !bot::kingSeedHolds(before.w, before.b),
                  "a round earlier, far below the ceiling, it doesn't hold");
    test::require(splitChild(before) == P.childLength, "and splits as before");
    bot::reportKing(before.w, before.b);
    test::require(!before.b.bestKing(before.w.round, P.kingReportAge, -1), "nor reports itself");
}

void kingHoldsNearTheCeiling() {
    if (!P.kingSeedOnValue)
        return;
    auto near = scene(150);
    near.w.unitCount = ceiling(near.w) - P.kingHoldLead;
    test::require(bot::kingSeedHolds(near.w, near.b) && splitChild(near) == 0,
                  "at r150 with the team kingHoldLead slots below the ceiling the king holds: a freed slot is refilled");
    auto below = scene(150);
    below.w.unitCount = ceiling(below.w) - P.kingHoldLead - 1;
    test::require(!bot::kingSeedHolds(below.w, below.b) && splitChild(below) == P.childLength,
                  "one slot further below, its child adds a unit, so it splits");
}

void onlyTheKingHolds() {
    if (!P.kingSeedOnValue)
        return;
    auto heard = scene(P.kingSeedRound);
    heard.b.merge(kingRecord(P.kingSeedRound - 2, heard.w.tileOf(20, 30), 14, 2), P.kingSeedRound);
    test::require(!bot::kingSeedHolds(heard.w, heard.b) && splitChild(heard) == P.childLength,
                  "a dragon that heard of a longer king splits as before");
    auto stale = scene(P.kingSeedRound);
    stale.b.merge(kingRecord(P.kingSeedRound - P.kingReportAge - 1, stale.w.tileOf(20, 30), 14, 2),
                  P.kingSeedRound);
    test::require(bot::kingSeedHolds(stale.w, stale.b), "a report older than kingReportAge doesn't count");
    auto tie = scene(P.kingSeedRound);
    test::dragon(tie.w, 3, false, tie.w.tileOf(20, 12), 10);
    test::require(!bot::kingSeedHolds(tie.w, tie.b), "an equally long teammate in view with a lower id is king instead");
    auto shortest = scene(P.kingSeedRound, P.kingMinLength - 1);
    test::require(!bot::kingSeedHolds(shortest.w, shortest.b), "a dragon shorter than kingMinLength is no king");
}

void smallMapsPlayAsBefore() {
    // A 32 x 32 board is below the area; one kingSeedArea / 32 columns wide is exactly on it. The team's growth is
    // unknown here (a dragon with too few turns behind it), which never reads as a stall.
    bot::teamTrend = {};
    auto small = scene(P.kingSeedRound, 10, 32, 32);
    small.w.unitCount = ceiling(small.w) - 1;
    test::require(32 * 32 < P.kingSeedArea && !bot::kingSeedHolds(small.w, small.b) &&
                      splitChild(small) == P.childLength,
                  "on a 32 x 32 board the longest dragon splits as before, near the ceiling or not");
    bot::reportKing(small.w, small.b);
    test::require(!small.b.bestKing(small.w.round, P.kingReportAge, -1), "and reports nothing before the beacon");
    if (P.kingSeedOnValue && P.kingSeedArea % 32 == 0) {
        auto edge = scene(P.kingSeedRound, 10, P.kingSeedArea / 32, 32);
        test::require(bot::kingSeedHolds(edge.w, edge.b), "a board of exactly kingSeedArea tiles seeds its king");
        auto under = scene(P.kingSeedRound, 10, P.kingSeedArea / 32 - 1, 32);
        test::require(!bot::kingSeedHolds(under.w, under.b), "one column less doesn't");
    }
}

// On a 512-tile board (I1257's sealed halves) at r260 with 30 of 64 units, a team whose count over the rounds left (240,
// read from our own turns) is flat while it lost units holds the king's ordinary split and reports it; one flat with no
// loss (nobody splitting), one whose growth adds a unit by the end, one that lost so much it could be wiped out by the
// end, a dragon with fewer than P.intakeWindow turns behind it, or any dragon before the game's halfway round, splits as
// today.
void stalledTeamSeedsOnSmallMaps() {
    if (!P.kingSeedOnValue || !P.kingSeedStall)
        return;
    int const round = 260, left = unswbc::Constants::MAX_ROUNDS - round;
    auto flat = scene(round, 10, 32, 16);
    test::require(flat.w.N < P.kingSeedArea && flat.w.unitCount < ceiling(flat.w) - P.kingHoldLead,
                  "precondition: under the area and far below the ceiling");
    bot::Intake intake;
    for (int r = round - left - 5; r <= round; r++)
        intake.units.emplace_back(r, r < round - left ? 25 : r == round ? 30 : r >= round - 100 ? 33 : 30);
    bot::teamTrend = intake.unitTrend(round, left);
    test::require(bot::teamTrend.known && bot::teamTrend.rounds == left && bot::teamTrend.change == 0 &&
                      bot::teamTrend.losses == 3,
                  "the trend spans the rounds left: change 0, three units lost");
    test::require(bot::kingSeedHolds(flat.w, flat.b) && splitChild(flat) == 0,
                  "a flat count, births replacing losses: the king holds its split");
    bot::reportKing(flat.w, flat.b);
    test::require(flat.b.bestKing(flat.w.round, P.kingReportAge, -1), "and reports itself before the beacon");

    auto other = scene(round, 10, 32, 16);
    intake.units.erase(intake.units.begin(), intake.units.end() - P.intakeWindow);
    test::require(!intake.unitTrend(round, left).known, "a dragon with P.intakeWindow turns behind it reads no trend");
    int const young = 2 * P.intakeWindow;
    bot::teamTrend = {true, young, 0, 0};
    test::require(!bot::kingSeedHolds(other.w, other.b), "a flat count with no unit lost: nobody is splitting, no hold");
    bot::teamTrend = {true, young, 1, 4};
    test::require(!bot::kingSeedHolds(other.w, other.b) && splitChild(other) == P.childLength,
                  "a unit gained over a younger dragon's life projects to more by the end: it splits");
    bot::teamTrend = {true, left, -(other.w.unitCount - 1), other.w.unitCount};
    test::require(!bot::kingSeedHolds(other.w, other.b),
                  "a team that lost as many units as it has left but one over the rounds left keeps breeding");
    bot::teamTrend = {true, left, -(other.w.unitCount - 2), other.w.unitCount};
    test::require(bot::kingSeedHolds(other.w, other.b), "one that lost one fewer seeds");
    int const half = unswbc::Constants::MAX_ROUNDS / 2;
    auto early = scene(half - 1, 10, 32, 16);
    bot::teamTrend = {true, half - 1, 0, 4};
    test::require(!bot::kingSeedHolds(early.w, early.b), "before the game has run as long as it has left: no hold");
    bot::teamTrend = {};
}

// The fold's timing is the beacon's on every map: a seeding king's earlier report starts no fold.
void foldStillStartsAtTheBeacon() {
    int const start = bot::foldStart();
    auto s = scene(start - 1, 6);
    s.b.merge(kingRecord(start - 2, s.w.tileOf(20, 14), 30, 2), start - 1);
    bot::Targets targets;
    targets.update(s.w);
    bot::DistField field;
    test::require(!bot::foldTowardKing(s.w, s.b, targets, field).active,
                  "a round before foldStart a dragon holding a king report doesn't fold");
    s.w.round = start;
    targets.update(s.w);
    test::require(bot::foldTowardKing(s.w, s.b, targets, field).active, "at foldStart it does");
}

// A seeding king still splits its tail out of a dead end: the hold stops only ordinary splits.
void trappedKingStillSplitsOut() {
    if (!P.kingSeedOnValue)
        return;
    Scene s{test::world(64, 64), {}};
    s.w.myId = 5;
    s.w.round = P.kingSeedRound;
    s.w.unitCount = 30;
    s.b.init(s.w);
    std::vector<int> tiles;
    for (int x = 17; x >= 6; --x) {
        tiles.push_back(s.w.tileOf(x, 12));
        test::wall(s.w, s.w.tileOf(x, 12), 0);
        test::wall(s.w, s.w.tileOf(x, 12), 2);
    }
    test::body(s.w, tiles);
    test::wall(s.w, s.w.tileOf(17, 12), 1);
    test::require(bot::kingSeedHolds(s.w, s.b), "premise: the dragon is a seeding king");
    test::require(splitChild(s) == s.w.length - unswbc::Constants::MIN_SIZE,
                  "with every move fatal it splits its tail out");
}

// A seeding king refuses a pearl-filled dead end it would have to split out of; with mayFarm the scene is farmed.
void seedingKingSkipsFarms() {
    auto w = test::world(64, 64);
    std::vector<int> tiles;
    for (int i = 0; i < 5; ++i)
        tiles.push_back(w.tileOf(12 - i, 12));
    test::body(w, tiles);
    for (int x = 13; x <= 17; ++x) {
        test::wall(w, w.tileOf(x, 12), 0);
        test::wall(w, w.tileOf(x, 12), 2);
        test::pearl(w, w.tileOf(x, 12));
    }
    test::wall(w, w.tileOf(17, 12), 1);
    w.unitCount = 30;
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    targets.update(w);
    test::require(bot::bestOf(bot::evaluateMoves(w, safety, targets, true)).dir == 1,
                  "a dragon that may farm enters the pearl-filled pocket");
    auto const held = bot::evaluateMoves(w, safety, targets, false);
    test::require(bot::bestOf(held).dir != 1 && held[1].closed && !held[1].farm,
                  "without mayFarm the pocket pays its shortfall and an open move wins");
}

// With P.kingFoldSkipsFarms the fold-window king refuses farms on every map; before the fold a small map's longest
// dragon still farms.
void foldKingSkipsFarmsOnEveryMap() {
    int const holdRound = bot::foldStart();
    auto king = scene(holdRound, 10, 32, 32);
    test::require(bot::kingSkipsFarms(king.w, king.b) == (P.kingFoldSkipsFarms && P.kingHoldsSplits),
                  "on a 32 x 32 board the fold-window king skips farms (with kingFoldSkipsFarms)");
    auto early = scene(P.kingSeedRound, 10, 32, 32);
    test::require(!bot::kingSkipsFarms(early.w, early.b), "before the beacon on a small board it farms");
}

// Directions a king's sonar carries its own report on this turn, of those it sends, with 2-long teammates in view
// north, east and south for the rays to reach and teammates' reports of three pearl blocks waiting to be relayed.
std::pair<int, int> beaconRays(Scene& s) {
    test::dragon(s.w, 6, false, s.w.tileOf(20, 4), 2);
    test::dragon(s.w, 7, false, s.w.tileOf(24, 8), 2);
    test::dragon(s.w, 8, false, s.w.tileOf(20, 12), 2);
    bot::reportKing(s.w, s.b);
    for (int k = 0; k < 3; ++k) {
        bot::Record r;
        r.round = s.w.round - 1;
        r.where = s.b.blockOf(s.w.tileOf(40 + 8 * k, 40));
        r.seenMask = 1;
        r.pearlMask = 1;
        s.b.merge(r, s.w.round);
    }
    bot::Gossip g;
    g.plan(s.w, s.b, s.w.head, 3);
    int kings = 0, sent = 0;
    for (int dir = 0; dir < 4; ++dir) {
        if (!g.sending[dir])
            continue;
        ++sent;
        auto const r = bot::decodeRecord(g.out[dir], s.b.context());
        kings += r && r->king;
    }
    return {kings, sent};
}

// A king seeding before the beacon sends its report on one ray and relays pearls on the rest; from the beacon its
// report takes every ray, as before.
void seedingBeaconLeavesRaysForPearls() {
    if (!P.kingSeedOnValue || !P.teamGossip)
        return;
    auto seeding = scene(P.kingSeedRound);
    auto const [kings, sent] = beaconRays(seeding);
    test::require(kings == 1 && sent > 1, "a seeding king's own report goes out on one ray, pearls on the others");
    auto beacon = scene(P.beaconRound);
    auto const [allKings, allSent] = beaconRays(beacon);
    test::require(allSent > 1 && allKings == allSent, "from the beacon its report goes out on every ray");
}

} // namespace

int main() {
    return test::suite({
        {"king holds from the seed round", kingHoldsFromTheSeedRound},
        {"king holds near the ceiling", kingHoldsNearTheCeiling},
        {"only the king holds", onlyTheKingHolds},
        {"small maps play as before", smallMapsPlayAsBefore},
        {"a stalled team seeds on small maps", stalledTeamSeedsOnSmallMaps},
        {"fold still starts at the beacon", foldStillStartsAtTheBeacon},
        {"trapped king still splits out", trappedKingStillSplitsOut},
        {"seeding king skips farms", seedingKingSkipsFarms},
        {"fold king skips farms on every map", foldKingSkipsFarmsOnEveryMap},
        {"seeding beacon leaves rays for pearls", seedingBeaconLeavesRaysForPearls},
    });
}
