#include "belief.hpp"
#include "census.hpp"
#include "gossip.hpp"
#include "support.hpp"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <vector>

using test::require;

namespace {

// Dragon `id`'s world at `round`: only the 7 x 7 window around (8, 8) in sight, so every ray may reach a teammate.
bot::World windowed(int id, int round) {
    auto w = test::world();
    w.myId = id;
    w.round = round;
    w.touched.clear();
    for (int t = 0; t < w.N; t++)
        w.visStamp[t] = 0;
    for (int dy = -3; dy <= 3; dy++) {
        for (int dx = -3; dx <= 3; dx++) {
            int const t = w.tileOf(8 + dx, 8 + dy);
            w.visStamp[t] = w.stamp;
            w.touched.push_back(t);
        }
    }
    return w;
}

// Everything a dragon plans to send this turn, as plain values (before wire::seal).
std::vector<std::uint64_t> sent(bot::World const& w, bot::Belief& b) {
    bot::Gossip g;
    g.plan(w, b, w.head, -1);
    std::vector<std::uint64_t> out;
    for (int dir = 0; dir < 4; dir++)
        if (g.sending[dir])
            out.push_back(g.out[dir]);
    return out;
}

// Two dragons that saw different enemy heads: after the first tells the second over sonar (its ids union one turn,
// its counts the next: one census record a turn), the second's estimate rests on both and holds the union of the ids.
void two_dragons_report_the_union_of_their_sightings() {
    // Three enemy ids from 50 up whose sketch bits differ, so the union's count is exact.
    std::vector<int> ids;
    for (int id = 50; ids.size() < 3; id++)
        if (std::none_of(ids.begin(), ids.end(), [&](int o) { return bot::censusBit(o) == bot::censusBit(id); }))
            ids.push_back(id);
    std::vector<int> const theirs = {ids[0], ids[1]}, mine = {ids[2]};
    bot::Census teller, listener;
    bot::World wt = windowed(1, 100), wl = windowed(2, 100);
    bot::Belief bt, bl;
    bt.init(wt);
    bl.init(wl);
    for (int round = 100; round <= 101; round++) {
        wt = windowed(1, round);
        wl = windowed(2, round);
        test::dragon(wt, theirs[0], true, wt.tileOf(9, 8), 3);
        test::dragon(wt, theirs[1], true, wt.tileOf(7, 8), 9);
        test::dragon(wl, mine[0], true, wl.tileOf(8, 9), 2);
        teller.observe(wt, bt.census);
        listener.observe(wl, bl.census);
        bl.hear(wl, sent(wt, bt));
    }
    bot::CensusEstimate const est = bl.census.estimate(101);
    require(est.senders == 2, "the counts rest on both dragons");
    require(bot::censusBit(1, bot::CENSUS_FROM_BITS) == bot::censusBit(2, bot::CENSUS_FROM_BITS) ||
                std::abs(est.observers - 2) < 0.5,
            "both dragons saw the union's enemies");
    // Whichever of the teller's counts arrived: each of its turns saw 2 heads (3 and 9 long), 1 short; the listener's
    // saw 1 head, short. So short heads per dragon-turn pool to 1 either way.
    require(est.longest == 9, "the longest is the longest either saw");
    require(std::abs(est.shortRate() - 1.0) < 1e-9, "short heads per dragon-turn pool over both dragons' turns");
    require(std::abs(est.ids - 3) < 0.5, "the union of their enemy ids: three distinct dragons");
}

// A sender's record counts while it is younger than P.censusWindow rounds and drops out after.
void a_record_older_than_the_window_drops_out() {
    bot::CensusTable table;
    bot::CensusRecord r;
    r.round = 200;
    r.sender = 7;
    r.turns = 10;
    r.heads = 3;
    r.shorts = 1;
    r.longest = 5;
    require(table.merge(r, 200), "a new sender's record is kept");
    r.round = 199;
    require(!table.merge(r, 200), "an older record of the same sender never replaces a newer one");
    int const last = 200 + P.censusWindow - 1;
    require(table.estimate(last).senders == 1 && table.estimate(last).turns == 10, "counted inside the window");
    require(table.estimate(last + 1).senders == 0 && table.estimate(last + 1).shortRate() < 0,
            "gone once the window has passed");

    // The ids union of an epoch counts through the next epoch and is gone the one after; a union record naming an epoch
    // older than the last is refused.
    int const epochRounds = P.censusWindow / 2, e = bot::censusEpoch(200);
    bot::CensusRecord u;
    u.ids = true;
    u.round = 200;
    u.epoch = e;
    u.from = 1;
    u.sketch = 1;
    require(table.merge(u, 200) && std::abs(table.estimate(200).ids - 1) < 0.5, "a heard union counts");
    require(!table.merge(u, 201), "hearing it again teaches nothing");
    int const nextEnd = (e + 2) * epochRounds - 1;
    require(std::abs(table.estimate(nextEnd).ids - 1) < 0.5, "still counted through the next epoch");
    require(table.estimate(nextEnd + 1).ids == 0, "gone the epoch after");
    u.sketch = 2;
    require(!table.merge(u, nextEnd + 1), "a union two epochs old is refused");
}

// Census records ride only rays the belief reports leave spare. With P.censusFlood, before the fold's start, a union
// whose ids count grew past the estimate's noise takes every spare ray; one new id among many known waits for the
// resend, as a union with nothing new does; from the fold's start a union takes one spare ray.
void census_rides_spare_rays_and_floods_only_past_its_noise() {
    // Every census record on the air, and every ray used.
    auto const onAir = [](bot::World const& w, bot::Belief& b) {
        int unions = 0, rays = 0;
        for (auto v : sent(w, b)) {
            rays++;
            if (auto r = bot::decodeRecord(v, b.context()); r && r->census && r->cen.ids)
                unions++;
        }
        return std::pair{unions, rays};
    };
    auto const withUnion = [](bot::Belief& b, int round) -> auto& {
        auto& slot = b.census.epoch(bot::censusEpoch(round));
        slot.from = 1;
        slot.sketch = 1;
        return slot;
    };

    auto w = windowed(1, 100);
    bot::Belief viewed;
    viewed.init(w);
    viewed.observe(w); // a view for each of the four blocks our window touches, one per useful ray
    withUnion(viewed, 100);
    auto const [crowded, crowdedRays] = onAir(w, viewed);
    require(crowded == 0 && crowdedRays == 4, "views keep every ray: the union waits for a spare one");

    bot::Belief b;
    b.init(w);
    auto& slot = withUnion(b, 100);
    auto const [first, all] = onAir(w, b);
    require(first == all && all == 4, "a first id floods every spare ray");
    w.round = 101;
    slot.sketch = (1U << 20) - 1; // 20 ids now
    auto const [many, again] = onAir(w, b);
    require(many == again && many == 4, "many new ids flood");
    require(!b.census.toSend(102), "nothing new: nothing to send before the resend");
    slot.sketch |= 1U << 20; // one more among 20: within the estimate's noise
    require(bot::sketchCount(slot.sketch) - bot::sketchCount(slot.sentSketch) <=
                bot::sketchError(bot::sketchCount(slot.sentSketch)),
            "the test's news is within the noise");
    require(!b.census.toSend(102), "news within the noise waits");
    require(b.census.toSend(101 + P.censusResend).has_value(), "and goes with the resend");

    auto const late = windowed(1, bot::foldStart());
    bot::Belief lb;
    lb.init(late);
    withUnion(lb, late.round);
    auto const [unions, lateRays] = onAir(late, lb);
    require(unions == 1 && lateRays == 1, "from the fold's start a first id takes one spare ray");
}

// Census records round-trip at their widest values; a heard forgery (the other team's copy of this bot, a flipped bit)
// teaches nothing, as with the belief's records; and decode refuses a counts record whose fields contradict each other
// and a union from an epoch after its own round.
void census_records_round_trip_and_forgeries_fail() {
    bot::Record counts;
    counts.census = true;
    counts.cen = {.ids = false, .round = 499, .sender = 4095, .turns = 63, .heads = 127, .shorts = 127, .longest = 63};
    bot::Record ids;
    ids.census = true;
    ids.cen = {.ids = true,
               .round = 321,
               .epoch = (bot::censusEpoch(321) - 1) & ((1 << bot::CENSUS_EPOCH_BITS) - 1),
               .from = (1U << bot::CENSUS_FROM_BITS) - 1,
               .sketch = (1U << bot::CENSUS_SKETCH_BITS) - 1};
    for (bool teamB : {false, true}) {
        for (auto const& r : {counts, ids}) {
            auto const back = bot::decodeRecord(bot::encodeRecord(r, test::wire(teamB)), test::wire(teamB));
            require(back && back->census && !back->king && !back->countdown && back->round == r.cen.round &&
                        back->cen.ids == r.cen.ids && back->cen.sender == r.cen.sender &&
                        back->cen.turns == r.cen.turns && back->cen.heads == r.cen.heads &&
                        back->cen.shorts == r.cen.shorts && back->cen.longest == r.cen.longest &&
                        back->cen.epoch == r.cen.epoch && back->cen.from == r.cen.from &&
                        back->cen.sketch == r.cen.sketch,
                    "a census record round-trips");
        }
    }
    bot::Record liar = counts;
    liar.cen.shorts = 5;
    liar.cen.heads = 4;
    require(!bot::decodeRecord(bot::encodeRecord(liar, test::wire(false)), test::wire(false)),
            "more short heads than heads is no record of ours");
    bot::Record late = ids;
    late.cen.epoch = (bot::censusEpoch(321) + 1) & ((1 << bot::CENSUS_EPOCH_BITS) - 1);
    require(!bot::decodeRecord(bot::encodeRecord(late, test::wire(false)), test::wire(false)),
            "a union from an epoch after its own round is no record of ours");

    auto w = windowed(3, 100);
    bot::Belief b;
    b.init(w);
    counts.cen.round = 99;
    b.hear(w, {bot::encodeRecord(counts, test::wire(!w.teamB)), bot::encodeRecord(counts, test::wire(w.teamB)) ^ 2});
    require(b.census.entries.empty(), "a forged census teaches nothing");
    b.hear(w, {bot::encodeRecord(counts, test::wire(w.teamB))});
    require(b.census.estimate(100).senders == 1, "the true one is merged");
}

} // namespace

int main() {
    return test::suite({
        {"census two dragons report the union of their sightings", two_dragons_report_the_union_of_their_sightings},
        {"census a record older than the window drops out", a_record_older_than_the_window_drops_out},
        {"census rides spare rays and floods only past its noise", census_rides_spare_rays_and_floods_only_past_its_noise},
        {"census records round trip and forgeries fail", census_records_round_trip_and_forgeries_fail},
    });
}
