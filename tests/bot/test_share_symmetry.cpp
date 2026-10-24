#include "gossip.hpp"
#include "support.hpp"
#include "symmetry.hpp"

using test::require;

namespace {

bot::Record countdown(bot::World const& w, int symmetry) {
    bot::Record r;
    r.countdown = true;
    r.round = w.round;
    r.where = w.head;
    r.wait = 5;
    r.symmetry = symmetry;
    return r;
}

void wire_keeps_the_mirror_and_unknown_is_zero() {
    auto w = test::world();
    auto const ctx = test::wire(w.teamB);
    for (int k = -1; k < 3; k++) {
        auto const value = bot::encodeRecord(countdown(w, k), ctx);
        auto const rec = bot::decodeRecord(value, ctx);
        require(rec && rec->countdown && rec->symmetry == k && rec->wait == 5 && rec->where == w.head,
                "every mirror and unknown round-trip without disturbing the countdown");
        require(((value >> 32) & 3) == static_cast<unsigned>(k + 1), "unknown uses zero, mirrors use one to three");
    }
    // The tag has 64 - wire::BODY_BITS bits and its key hashes the bot's own source (sonarkey.hpp), so one flipped
    // record in 2^13 still decodes by chance, whatever the build: over 2 mirrors x 16 heads at most one may. Mirror
    // 2 is left out: flipping bit 32 turns mirror 1's body into mirror 2's and back, so it repeats mirror 1's chance.
    int passed = 0;
    for (int k = 0; k < 2; k++) {
        for (int t = 0; t < 16; t++) {
            auto record = countdown(w, k);
            record.where = w.tileOf(t, t);
            passed += bot::decodeRecord(bot::encodeRecord(record, ctx) ^ (1ULL << 32), ctx) ? 1 : 0;
        }
    }
    require(passed <= 1, "the tag covers mirror bits");
    bot::Record king;
    king.king = true;
    king.round = w.round;
    king.where = w.head;
    king.id = 65535;
    king.length = 80;
    king.teamB = true;
    king.heir = true;
    auto const rec = bot::decodeRecord(bot::encodeRecord(king, ctx), ctx);
    require(rec && rec->king && rec->id == king.id && rec->length == king.length && rec->teamB && rec->heir &&
                rec->symmetry == -1, "king layout remains unchanged");
    auto body = bot::encodeRecord(countdown(w, 0), ctx) & bot::wire::BODY_MASK;
    body |= 1ULL << 34;
    require(!bot::decodeRecord(body | bot::wire::tagOf(body, ctx) << bot::wire::BODY_BITS, ctx),
            "reserved countdown bits still reject a tagged value");
}

void invalid_records_cannot_teach_a_mirror() {
    auto w = test::world();
    for (int kind = 0; kind < 4; kind++) {
        bot::Belief b;
        b.init(w);
        auto r = countdown(w, 0);
        if (kind == 0) r.round = w.round + 1;
        if (kind == 1) r.round = w.round - std::max(P.gossipMaxAge, P.kingReportAge) - 2;
        if (kind == 2) r.where = w.N;
        auto ctx = b.context();
        if (kind == 3) ctx.teamB = !ctx.teamB;
        b.hear(w, {bot::encodeRecord(r, ctx)});
        require(b.sharedSymmetry < 0, "future, stale, out-of-map and other-team claims are ignored");
    }
    bot::Belief b;
    b.init(w);
    b.hear(w, {bot::encodeRecord(countdown(w, -1), b.context())});
    require(b.sharedSymmetry < 0, "legacy zero bits teach no mirror");
}

void split_neck_ray_teaches_the_child_even_with_a_king_beacon() {
    auto w = test::world();
    test::body(w, {w.tileOf(8, 8), w.tileOf(7, 8), w.tileOf(6, 8), w.tileOf(5, 8)});
    w.symmetry = 0;
    bot::Belief b;
    b.init(w);
    b.setSpawn(w.head, w.round + 5, w.round);
    b.spawnSent[w.head] = w.round; // no fresh unsent countdown: fall back to this occupied head tile
    bot::Record king;
    king.king = true;
    king.id = w.myId;
    king.round = w.round;
    king.length = w.length;
    king.where = w.head;
    b.mergeKing(king);
    bot::Gossip g;
    int const neck = bot::neckDir(w);
    g.plan(w, b, w.head, neck, true);
    require(g.sending[neck], "split sends through the neck");
    auto const rec = bot::decodeRecord(g.out[neck], b.context());
    if (P.shareSymmetry) {
        require(rec && rec->countdown && rec->symmetry == w.symmetry && rec->where == w.head,
                "the child's ray carries our mirror on a true countdown ahead of the king beacon");
        auto child = test::world();
        child.symmetry = -1;
        bot::Belief cb;
        cb.init(child);
        cb.hear(child, {g.out[neck]});
        bot::readSymmetry(child, cb);
        require(child.symmetry == w.symmetry, "the fresh child knows the mirror before choosing its first move");
    } else {
        require(rec && rec->king, "disabled sharing leaves the ordinary king beacon");
    }
}

void unknown_mirrors_and_barren_views_keep_ordinary_gossip() {
    for (bool known : {false, true}) {
        auto w = test::world();
        w.symmetry = known ? 0 : -1;
        test::body(w, {w.tileOf(8, 8), w.tileOf(7, 8), w.tileOf(6, 8), w.tileOf(5, 8)});
        bot::Belief b;
        b.init(w);
        b.observe(w);
        if (!known) b.setSpawn(w.head, w.round + 5, w.round);
        auto copy = b;
        bot::Gossip normal, split;
        normal.plan(w, b, w.head, bot::neckDir(w));
        split.plan(w, copy, w.head, bot::neckDir(w), true);
        require(normal.sending == split.sending && normal.out == split.out,
                "unknown mirrors or no real countdown do not change the scheduled messages");
    }
}

void a_countdown_relays_the_mirror_on_normal_moves() {
    auto w = test::world();
    w.symmetry = 2;
    bot::Belief b;
    b.init(w);
    b.setSpawn(w.head, w.round + 1, w.round);
    bot::Gossip g;
    g.plan(w, b, w.head, 3);
    require(g.sending[3], "ordinary move sends its countdown through the neck");
    auto const rec = bot::decodeRecord(g.out[3], b.context());
    require(rec && rec->countdown && rec->symmetry == (P.shareSymmetry ? 2 : -1), "normal countdowns relay the known mirror");
    b.spawnSent[w.head] = w.round;
    g.plan(w, b, w.head, 3, true);
    require(g.sending[3] == P.shareSymmetry, "a split can send its claim even when no unsent reports remain");
}

} // namespace

int main() {
    return test::suite({
        {"wire keeps the mirror and unknown is zero", wire_keeps_the_mirror_and_unknown_is_zero},
        {"invalid records cannot teach a mirror", invalid_records_cannot_teach_a_mirror},
        {"split neck ray teaches the child even with a king beacon", split_neck_ray_teaches_the_child_even_with_a_king_beacon},
        {"unknown mirrors and barren views keep ordinary gossip", unknown_mirrors_and_barren_views_keep_ordinary_gossip},
        {"a countdown relays the mirror on normal moves", a_countdown_relays_the_mirror_on_normal_moves},
    });
}
