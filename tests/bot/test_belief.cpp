#include "belief.hpp"
#include "gossip.hpp"
#include "support.hpp"
#include "targets.hpp"

#include <cmath>
#include <cstdint>
#include <vector>

using test::require;

namespace {

// A board last seen long ago except the 7 x 7 window around our head, which is this turn's vision.
bot::World windowed(int headX, int headY, int round = 100) {
    auto w = test::world();
    w.round = round;
    w.head = w.tileOf(headX, headY);
    w.touched.clear();
    for (int t = 0; t < w.N; t++) {
        w.visStamp[t] = 0;
        w.lastSeen[t] = 40;
    }
    for (int dy = -3; dy <= 3; dy++) {
        for (int dx = -3; dx <= 3; dx++) {
            int const t = w.tileOf((headX + dx + w.W) % w.W, (headY + dy + w.H) % w.H);
            w.visStamp[t] = w.stamp;
            w.lastSeen[t] = round;
            w.touched.push_back(t);
        }
    }
    return w;
}

bot::Belief beliefOf(bot::World const& w) {
    bot::Belief b;
    b.init(w);
    b.observe(w);
    return b;
}

// A view of every cell of the block holding `tile`, as a teammate reports it.
bot::Record view(bot::Belief const& b, int tile, int round, bool pearlOnTile, bool enemy = false) {
    bot::Record r;
    r.round = round;
    r.where = b.blockOf(tile);
    for (int i = 0; i < bot::BLOCK * bot::BLOCK; i++) {
        int const t = b.cellTile(r.where, i);
        if (t < 0)
            continue;
        r.seenMask |= 1 << i;
        if (pearlOnTile && t == tile)
            r.pearlMask |= 1 << i;
    }
    r.enemy = enemy;
    return r;
}

// Everything a dragon plans to send this turn after moving to `origin`, as plain values (before wire::seal).
std::vector<std::uint64_t> sent(bot::World const& w, bot::Belief& b, int origin) {
    bot::Gossip g;
    g.plan(w, b, origin, -1);
    std::vector<std::uint64_t> out;
    for (int dir = 0; dir < 4; dir++)
        if (g.sending[dir])
            out.push_back(g.out[dir]);
    return out;
}

void records_round_trip_and_reject_forgeries() {
    bot::Record v;
    v.round = 321;
    v.where = 255;
    v.seenMask = 0xffff;
    v.pearlMask = 0x8001;
    v.enemy = true;
    bot::Record c;
    c.countdown = true;
    c.round = 499;
    c.where = 4095;
    c.wait = 17;
    for (bool teamB : {false, true}) {
        auto const back = bot::decodeRecord(bot::encodeRecord(v, test::wire(teamB)), test::wire(teamB));
        require(back && !back->countdown && back->round == 321 && back->where == 255 && back->seenMask == 0xffff &&
                    back->pearlMask == 0x8001 && back->enemy,
                "a view must survive encoding for both teams");
        auto const cd = bot::decodeRecord(bot::encodeRecord(c, test::wire(teamB)), test::wire(teamB));
        require(cd && cd->countdown && cd->round == 499 && cd->where == 4095 && cd->wait == 17,
                "a countdown must survive encoding for both teams");
        require(!bot::decodeRecord(bot::encodeRecord(v, test::wire(teamB)) ^ (1ULL << 20), test::wire(teamB)),
                "a flipped body bit fails the tag");
        require(!bot::decodeRecord(18446744073709551615ULL, test::wire(teamB)) && !bot::decodeRecord(1806, test::wire(teamB)),
                "an enemy's arbitrary values are not records");
        bot::Record liar = v;
        liar.seenMask = 1;
        liar.pearlMask = 2;
        require(!bot::decodeRecord(bot::encodeRecord(liar, test::wire(teamB)), test::wire(teamB)),
                "a pearl on a cell the report did not see is garbage");
    }
}

// A copy of this bot on the other team (the champion or an old version in local tests) must not feed our belief
// map: its views and countdowns, valid records for its own side, fail our tag.
void other_teams_records_are_rejected() {
    bot::Record v;
    v.round = 200;
    v.where = 17;
    v.seenMask = 0x00f0;
    v.pearlMask = 0x0010;
    bot::Record c;
    c.countdown = true;
    c.round = 200;
    c.where = 1234;
    c.wait = 5;
    for (bool teamB : {false, true}) {
        for (auto const& r : {v, c}) {
            require(!bot::decodeRecord(bot::encodeRecord(r, test::wire(!teamB)), test::wire(teamB)), "the other side's record is rejected");
        }
    }

    auto w = windowed(8, 8);
    auto b = beliefOf(w);
    int const far = w.tileOf(1, 1); // out of our sight
    bot::Record rec = view(b, far, 95, true);
    b.hear(w, {bot::encodeRecord(rec, test::wire(!w.teamB))});
    require(b.seen[far] == -1 && !b.pearlAt(far), "hearing the other team's view teaches nothing");
    b.hear(w, {bot::encodeRecord(rec, test::wire(w.teamB))});
    require(b.seen[far] == 95 && b.pearlAt(far), "the same view from our own team is merged");
}

// SipHash-2-4's reference output for key 00..0f and message 00..0f (the paper's vectors, index 16).
void siphash_matches_the_reference() {
    require(bot::wire::sipHash(0x0706050403020100ULL, 0x0f0e0d0c0b0a0908ULL, 0x0706050403020100ULL,
                               0x0f0e0d0c0b0a0908ULL) == 0x3f2acc7f57c29bdbULL,
            "SipHash-2-4 of 16 bytes matches the reference vector");
}

// Values that are valid on some other build's or game's wire are not ours: the champion's tag (splitmix over the
// body and side under the fixed KEY, which every version before this one shares), a record for a map of another
// size, and random values, which pass the 13-bit tag about once in 8192.
void other_builds_and_maps_are_rejected() {
    require(bot::wire::HAS_BUILD_KEY && bot::wire::KEY0 != bot::wire::KEY, "this build's key is its own");
    bot::Record v;
    v.round = 200;
    v.where = 17;
    v.seenMask = 0x00f0;
    v.pearlMask = 0x0010;
    for (bool teamB : {false, true}) {
        std::uint64_t const body = bot::encodeRecord(v, test::wire(teamB)) & bot::wire::BODY_MASK;
        std::uint64_t const side = teamB ? 2ULL : 1ULL;
        std::uint64_t const old = body | bot::wire::mix((body | side << bot::wire::BODY_BITS) ^ bot::wire::KEY) >>
                                             bot::wire::BODY_BITS << bot::wire::BODY_BITS;
        require(!bot::decodeRecord(old, test::wire(teamB)), "the previous version's record is rejected");
        require(bot::decodeRecord(bot::encodeRecord(v, test::wire(teamB, 40, 30)), test::wire(teamB, 40, 30)).has_value(),
                "a record round-trips on any map size");
        require(!bot::decodeRecord(bot::encodeRecord(v, test::wire(teamB, 17, 16)), test::wire(teamB)),
                "a record from a map of another width is rejected");
        require(!bot::decodeRecord(bot::encodeRecord(v, test::wire(teamB, 16, 17)), test::wire(teamB)),
                "a record from a map of another height is rejected");
    }
    int accepted = 0;
    std::uint64_t x = 12345;
    for (int i = 0; i < 1 << 16; i++)
        accepted += bot::decodeRecord(bot::wire::mix(x += 0x9e3779b97f4a7c15ULL), test::wire(false)).has_value();
    require(accepted <= 32, "random values almost never pass the tag");
}

// A sealed value (wire::seal) opens to the plain value in its send round and the next, the two rounds a sonar can
// be read in; the same record goes out as another body every round, and a value two rounds old, the other side's
// value or a flipped body bit opens to nothing.
void sealed_values_open_only_in_their_send_round_and_the_next() {
    bot::Record v;
    v.round = 200;
    v.where = 17;
    v.seenMask = 0x00f0;
    v.pearlMask = 0x0010;
    auto const ctx = test::wire(false);
    std::uint64_t const plain = bot::encodeRecord(v, ctx);
    for (int round : {200, 201}) {
        std::uint64_t const sent = bot::wire::seal(plain, round, ctx);
        require(bot::wire::open(sent, round, ctx) == plain && bot::wire::open(sent, round + 1, ctx) == plain,
                "a sealed value opens in its send round and the next");
        require(!bot::wire::open(sent, round + 2, ctx) && !bot::wire::open(sent, round - 1, ctx),
                "but not two rounds later or before it was sent");
        require(!bot::wire::open(sent, round, test::wire(true)), "the other side can't open it");
        require(!bot::wire::open(sent ^ 1ULL << 22, round, ctx), "a flipped body bit fails the check");
        require(bot::wire::openAll({sent ^ 1ULL << 22, sent}, round, ctx) == std::vector{plain},
                "and openAll drops it, keeping the good value");
    }
    require((bot::wire::seal(plain, 200, ctx) ^ bot::wire::seal(plain, 201, ctx)) & bot::wire::BODY_MASK,
            "the same record's body differs between rounds");
}

// A replayed record of ours is heard only while it could still be a teammate's: stale ones (older than any
// report we send, plus a round of relay) and ones from a later round are dropped. A true report replayed in time
// changes nothing, whatever arrived since.
void stale_future_and_repeated_records_change_nothing() {
    int const maxAge = std::max(P.gossipMaxAge, P.kingReportAge) + 1;
    auto w = windowed(8, 8);
    int const far = w.tileOf(1, 1); // out of our sight
    auto b = beliefOf(w);
    b.hear(w, {bot::encodeRecord(view(b, far, w.round - maxAge - 1, true), b.context())});
    require(b.seen[far] == -1, "a report older than we ever send is dropped");
    b.hear(w, {bot::encodeRecord(view(b, far, w.round + 1, true), b.context())});
    require(b.seen[far] == -1, "a report from a later round is dropped");
    std::uint64_t const oldest = bot::encodeRecord(view(b, far, w.round - maxAge, true), b.context());
    b.hear(w, {oldest});
    require(b.seen[far] == w.round - maxAge && b.pearlAt(far), "the oldest report a teammate can send is heard");

    std::uint64_t const pearl = bot::encodeRecord(view(b, far, w.round - 5, true), b.context());
    b.hear(w, {pearl});
    b.hear(w, {bot::encodeRecord(view(b, far, w.round - 2, false), b.context())});
    require(b.seen[far] == w.round - 2 && !b.pearlAt(far), "a fresher report clears the pearl");
    b.hear(w, {pearl, pearl, oldest});
    require(b.seen[far] == w.round - 2 && !b.pearlAt(far), "replaying the older reports brings nothing back");
}

void older_report_never_overwrites_a_fresher_cell() {
    auto w = windowed(8, 8);
    auto b = beliefOf(w);
    int const far = w.tileOf(1, 1); // out of our sight
    require(b.merge(view(b, far, 95, true), w.round), "a first report is news");
    require(b.pearlAt(far) && b.seen[far] == 95, "the report's pearl and stamp are stored");
    require(!b.merge(view(b, far, 90, false), w.round), "an older empty report teaches nothing");
    require(b.pearlAt(far) && b.seen[far] == 95, "and leaves the fresher pearl in place");
    require(b.merge(view(b, far, 97, false), w.round) && !b.pearlAt(far) && b.seen[far] == 97,
            "a fresher empty report clears the pearl");

    int const near = w.tileOf(9, 8); // in our sight this turn, empty
    require(!b.merge(view(b, near, 99, true), w.round), "a teammate's older sighting never beats our own vision");
    require(!b.pearlAt(near) && b.seen[near] == 100, "our empty view of the tile stands");
    require(!b.merge(view(b, far, 101, true), w.round), "a report dated after now is dropped");
}

void relayed_report_reaches_a_dragon_out_of_line_of_sight() {
    // A sees a pearl at (1,1); B is in A's sonar line but far from the pearl; C hears only B.
    auto a = windowed(2, 2);
    int const pearl = a.tileOf(1, 1);
    test::pearl(a, pearl);
    auto beliefA = beliefOf(a);
    auto const fromA = sent(a, beliefA, a.head);
    require(!fromA.empty(), "a dragon with open rays shares what it sees");

    auto b = windowed(12, 12);
    auto beliefB = beliefOf(b);
    beliefB.hear(b, fromA);
    require(beliefB.pearlAt(pearl) && beliefB.seen[pearl] == 100, "B learns A's pearl");
    auto const fromB = sent(b, beliefB, b.head);

    auto c = windowed(12, 4);
    auto beliefC = beliefOf(c);
    require(!beliefC.pearlAt(pearl), "C cannot see the pearl itself");
    beliefC.hear(c, fromB);
    require(beliefC.pearlAt(pearl) && beliefC.seen[pearl] == 100, "B's relay carries A's pearl and stamp to C");

    // B relays each report once, until a fresher one about the same block arrives.
    b.round = 101;
    auto const again = bot::Gossip::gather(b, beliefB);
    for (auto const& r : again)
        require(r.countdown || r.where != beliefB.blockOf(pearl), "a report already relayed is not relayed again");
    beliefB.merge(view(beliefB, pearl, 101, false), b.round);
    bool relayed = false;
    for (auto const& r : bot::Gossip::gather(b, beliefB))
        relayed = relayed || (!r.countdown && r.where == beliefB.blockOf(pearl) && r.round == 101);
    require(relayed, "a fresher report on the same block is relayed");
}

void rays_that_cannot_reach_a_teammate_stay_silent() {
    auto w = windowed(8, 8);
    auto b = beliefOf(w);
    test::dragon(w, 50, true, w.tileOf(8, 6), 3); // enemy two tiles north
    test::wall(w, w.head, 1);                     // kelp east
    test::dragon(w, 7, false, w.tileOf(8, 10), 3); // teammate two tiles south
    bot::Gossip g;
    g.plan(w, b, w.head, -1);
    require(!g.sending[0], "a ray that stops on an enemy is not sent");
    require(!g.sending[1], "a ray into kelp is not sent");
    require(g.sending[2], "a ray that hits a teammate is sent");
    require(g.sending[3], "a ray leaving our sight may still find a teammate");
}

void exploration_skips_a_region_a_teammate_just_saw() {
    auto w = windowed(8, 8);
    auto b = beliefOf(w);
    bot::Targets alone;
    alone.update(w);
    int const first = alone.explore;
    require(first == w.tileOf(8, 4), "on our own we explore the nearest stale grid tile");

    b.merge(view(b, first, 99, false), w.round);
    bot::Targets shared;
    shared.team = &b;
    shared.update(w);
    require(shared.explore >= 0 && b.blockOf(shared.explore) != b.blockOf(first),
            "a region a teammate saw last round is not worth the trip");
    require(shared.explore == w.tileOf(4, 8), "the next stale grid tile is chosen instead");

    // A target a teammate reports after we chose it is dropped at once, before our retarget timer.
    w.round = 101;
    b.merge(view(b, shared.explore, 101, false), w.round);
    int const before = shared.explore;
    shared.update(w);
    require(shared.explore != before, "a teammate just saw our target, so we pick another");
}

void shared_pearls_and_countdowns_become_targets() {
    auto w = windowed(8, 8);
    auto b = beliefOf(w);
    int const pearl = w.tileOf(8, 13); // five steps south, out of sight
    b.merge(view(b, pearl, 99, true), w.round);
    bot::Targets alone;
    alone.choosePearl(w);
    require(alone.pearl == -1, "without the team map we know no pearl");
    bot::Targets shared;
    shared.team = &b;
    shared.choosePearl(w);
    require(shared.pearl == pearl, "a teammate's pearl beyond our view is a target");

    b.merge(view(b, pearl, 100, false), w.round);
    shared.choosePearl(w);
    require(shared.pearl == -1, "a fresher report of the tile empty removes it");

    auto w2 = windowed(8, 8);
    auto b2 = beliefOf(w2);
    bot::Record cd;
    cd.countdown = true;
    cd.round = 95;
    cd.where = pearl;
    cd.wait = 3; // fired on round 98, after our own last look (round 40)
    require(b2.merge(cd, w2.round), "a countdown report is news");
    bot::Targets fromCountdown;
    fromCountdown.team = &b2;
    fromCountdown.choosePearl(w2);
    require(fromCountdown.pearl == pearl, "a shared countdown that fired unseen suggests a pearl");
    cd.round = 94;
    cd.wait = 60;
    require(!b2.merge(cd, w2.round), "an older countdown report never replaces a fresher one");
    w2.spawnKind[pearl] = bot::SPAWN_NEVER;
    fromCountdown.choosePearl(w2);
    require(fromCountdown.pearl == -1, "a tile we know never spawns ignores reported countdowns");
}

void enemy_belief_fades_toward_the_prior() {
    auto w = windowed(8, 8);
    auto b = beliefOf(w);
    int const block = b.blockOf(w.tileOf(1, 1));
    require(std::abs(b.enemyChance(block, 100) - P.enemyPrior) < 1e-9, "an unreported block sits at the prior");
    b.merge(view(b, w.tileOf(1, 1), 100, false, true), w.round);
    require(std::abs(b.enemyChance(block, 100) - 1.0) < 1e-9, "a fresh sighting is certain");
    double const later = b.enemyChance(block, 100 + static_cast<int>(P.enemyHalfLife));
    require(std::abs(later - (P.enemyPrior + (1 - P.enemyPrior) / 2)) < 1e-9,
            "after one half-life the evidence is halfway back to the prior");
    require(b.enemyChance(block, 400) - P.enemyPrior < 1e-6, "old sightings fade to the prior");

    int const other = b.blockOf(w.tileOf(13, 1));
    b.merge(view(b, w.tileOf(13, 1), 100, false, false), w.round);
    require(b.enemyChance(other, 100) < 1e-9 && b.enemyChance(other, 108) < P.enemyPrior,
            "a report of no enemy rises back toward the prior");
}

void partial_edge_blocks_reject_cells_past_the_map() {
    auto w = test::world(10, 10);
    bot::Belief b;
    b.init(w);
    bot::Record r;
    r.round = 90;
    r.where = 2; // x 8..11 on a 10-wide map
    r.seenMask = 1 << 3; // x 11 does not exist
    require(!b.merge(r, w.round), "a report of a cell past the map's edge is garbage");
    r.seenMask = 1 << 1;
    require(b.merge(r, w.round) && b.seen[w.tileOf(9, 0)] == 90, "the edge block's real cells merge");
}


// A decoded enemy head (listen.hpp) on ground we don't see becomes a sighting gossip relays; one on a tile we see this
// turn is not believed. A sender set inside one block is filed the same way; a set spread thinly only raises the chance.
void heard_enemy_heads_become_relayed_sightings() {
    auto w = windowed(8, 8);
    auto b = beliefOf(w);
    int const far = w.tileOf(1, 1), block = b.blockOf(far);
    require(b.noteEnemyHead(far, 97, w.round), "a decoded head on stale ground is new");
    require(b.enemyChance(block, 97) > 1 - 1e-9 && !b.pearlAt(far), "it is a sighting, and no pearl is under a head");
    bool relayed = false;
    for (auto const& r : bot::Gossip::gather(w, b))
        relayed = relayed || (!r.countdown && !r.king && r.where == block && r.enemy && r.round == 97);
    require(relayed, "gossip relays the decoded head like any sighting");
    require(!b.noteEnemyHead(w.tileOf(9, 9), 99, w.round), "a head we see no enemy on this turn is not believed");

    auto spread = beliefOf(w);
    std::vector<int> wide;
    for (int x = 0; x < w.W; x++)
        wide.push_back(w.tileOf(x, 13));
    spread.noteSenders(wide, w.round);
    int const b13 = spread.blockOf(w.tileOf(1, 13));
    require(spread.enemyRound[b13] < 0 && spread.enemyChance(b13, w.round) > P.enemyPrior &&
                spread.enemyChance(b13, w.round) < 0.5,
            "a set spread over many blocks raises each one's chance a little and files no sighting");
    auto narrow = beliefOf(w);
    narrow.noteSenders({w.tileOf(1, 13), w.tileOf(2, 13)}, w.round);
    require(narrow.enemyRound[b13] == w.round && narrow.enemyThere[b13], "a set inside one block is filed as a sighting");
}

} // namespace

int main() {
    return test::suite({
        {"belief records round trip and reject forgeries", records_round_trip_and_reject_forgeries},
        {"the other team's records are rejected", other_teams_records_are_rejected},
        {"sonar siphash matches the reference", siphash_matches_the_reference},
        {"sonar other builds' and maps' records are rejected", other_builds_and_maps_are_rejected},
        {"sonar sealed values open only in their send round and the next", sealed_values_open_only_in_their_send_round_and_the_next},
        {"sonar stale, future and repeated records change nothing", stale_future_and_repeated_records_change_nothing},
        {"belief older report never overwrites a fresher cell", older_report_never_overwrites_a_fresher_cell},
        {"belief relayed report reaches a dragon out of line of sight", relayed_report_reaches_a_dragon_out_of_line_of_sight},
        {"belief rays that cannot reach a teammate stay silent", rays_that_cannot_reach_a_teammate_stay_silent},
        {"belief exploration skips a region a teammate just saw", exploration_skips_a_region_a_teammate_just_saw},
        {"belief shared pearls and countdowns become targets", shared_pearls_and_countdowns_become_targets},
        {"belief enemy belief fades toward the prior", enemy_belief_fades_toward_the_prior},
        {"belief partial edge blocks reject cells past the map", partial_edge_blocks_reject_cells_past_the_map},
        {"belief heard enemy heads become relayed sightings", heard_enemy_heads_become_relayed_sightings},
    });
}
