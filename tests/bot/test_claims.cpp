// Pearl claims (claims.hpp): a teammate's claim heard over sonar takes a pearl it eats first, and the claim's wire
// pattern is one no other record decoder accepts.
#include "support.hpp"
#include "claims.hpp"
#include "doorway.hpp"
#include "gossip.hpp"
#include "targets.hpp"

#include <algorithm>

using test::require;

namespace {

void a_claim_that_eats_first_takes_the_pearl() {
    auto w = test::world();
    w.head = w.tileOf(4, 4);
    int const claimed = w.tileOf(7, 4), other = w.tileOf(4, 8); // 3 and 4 steps from us
    test::pearl(w, claimed);
    test::pearl(w, other);
    auto const ctx = test::wire(false);
    auto const hear = [&](int eatIn, int id) {
        bot::Claims claims;
        claims.hear(w, ctx, {bot::encodeClaim({.eat = w.round + eatIn, .tile = claimed, .id = id}, ctx)});
        bot::Targets targets;
        targets.claims = &claims;
        targets.choosePearl(w);
        return targets.pearl;
    };
    require(hear(2, 9) == other, "a teammate out of view that eats it in 2 takes it from our 3");
    require(hear(4, 9) == claimed, "one that eats it later leaves it to us");
    require(hear(3, 0) == other && hear(3, 9) == claimed, "an equal arrival goes to the lower id");
    bot::Claims stale;
    stale.hear(w, ctx, {bot::encodeClaim({.eat = w.round - 1, .tile = claimed, .id = 9}, ctx)});
    require(!stale.beaten(w, claimed, 3), "a claim whose eat round has passed is dropped");
}

void only_the_claim_decoder_reads_a_claim() {
    auto const ctx = test::wire(true);
    std::uint64_t const value = bot::encodeClaim({.eat = 321, .tile = 3000, .id = 5000}, ctx);
    auto const c = bot::decodeClaim(value, ctx);
    require(c && c->eat == 321 && c->tile == 3000 && c->id == 5000, "a claim decodes to what was sent");
    require(!bot::decodeRecord(value, ctx) && !bot::decodeProbe(value, ctx), "no belief record or doorway probe");
    require(!bot::decodeClaim(value, test::wire(false)), "the other side's copy of the bot can't read it");
    bot::Record view;
    view.round = 100;
    view.where = 3;
    view.seenMask = 1;
    require(!bot::decodeClaim(bot::encodeRecord(view, ctx), ctx), "a real view is no claim");
}

// A new claim outranks unsent reports of barren ground for a ray even when those would fill every ray, a repeat of it
// waits behind them, and a teammate that hears it yields the pearl.
void a_new_claim_takes_a_ray_from_full_gossip() {
    auto w = test::world();
    w.round = 50;
    w.head = w.tileOf(8, 8);
    w.touched.clear();
    for (int t = 0; t < w.N; t++)
        w.touched.push_back(t);
    test::dragon(w, 2, false, w.tileOf(8, 4), 2); // a teammate on every ray
    test::dragon(w, 3, false, w.tileOf(12, 8), 2);
    test::dragon(w, 4, false, w.tileOf(8, 12), 2);
    test::dragon(w, 5, false, w.tileOf(4, 8), 2);
    bot::Belief b;
    b.init(w);
    b.observe(w);
    require(bot::Gossip::gather(w, b, 4).size() == 4, "the belief has a report for every ray");
    bot::Gossip g;
    int const tile = w.tileOf(10, 10);
    g.claim = bot::PearlClaim{.eat = w.round + 4, .tile = tile, .id = w.myId};
    auto const onAir = [&](std::vector<std::uint64_t>& heard) {
        g.plan(w, b, w.head, -1);
        heard.clear();
        for (int dir = 0; dir < 4; dir++)
            if (g.sending[dir])
                heard.push_back(g.out[dir]);
        return std::count_if(heard.begin(), heard.end(),
                             [&](std::uint64_t v) { return bot::decodeClaim(v, b.context()).has_value(); });
    };
    std::vector<std::uint64_t> heard, again;
    require(onAir(heard) >= 1, "the claim is on the air");
    w.round++;
    b.observe(w);
    require(onAir(again) == 0, "the same claim a round later waits behind reports not yet sent");
    auto mate = w;
    mate.round--;
    mate.myId = w.myId + 1;
    bot::Claims claims;
    claims.hear(mate, b.context(), heard);
    require(claims.beaten(mate, tile, 5), "a teammate 5 steps away yields it to our 4");
}

} // namespace

int main() {
    return test::suite({
        {"a claim that eats first takes the pearl", a_claim_that_eats_first_takes_the_pearl},
        {"only the claim decoder reads a claim", only_the_claim_decoder_reads_a_claim},
        {"a new claim takes a ray from full gossip", a_new_claim_takes_a_ray_from_full_gossip},
    });
}
