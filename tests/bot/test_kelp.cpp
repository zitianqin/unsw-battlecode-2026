#include "gossip.hpp"
#include "support.hpp"
#include "targets.hpp"

using test::require;

namespace {

std::uint64_t blockPacket(int block, std::uint32_t mask, int round, bot::wire::Context const& ctx) {
    auto const body = 1ULL | std::uint64_t(round) << 1 | std::uint64_t(block) << 10 |
                      std::uint64_t(mask) << 18 | 1ULL << 50;
    return body | bot::wire::tagOf(body, ctx) << bot::wire::BODY_BITS;
}

void sealed_room_is_known_from_birth() {
    auto child = test::world();
    child.setAllEdges(bot::EDGE_UNKNOWN);
    std::fill(child.lastSeen.begin(), child.lastSeen.end(), -1);
    child.head = child.tileOf(8, 8);
    bot::Belief b;
    b.init(child);
    std::vector<std::uint32_t> masks(b.blocks(), 0);
    auto wall = [&](int t, int d) {
        int const e = child.edgeOf(t, d), cell = e % child.N;
        int const bit = (e / child.N) * 16 + child.yOf(cell) % bot::BLOCK * bot::BLOCK + child.xOf(cell) % bot::BLOCK;
        masks[b.blockOf(cell)] |= 1U << bit;
    };
    for (int i = 4; i < 12; ++i) {
        wall(child.tileOf(i, 4), 0);
        wall(child.tileOf(i, 11), 2);
        wall(child.tileOf(4, i), 3);
        wall(child.tileOf(11, i), 1);
    }
    bot::Targets before;
    before.scanRegion(child);
    require(before.regionQueue.size() == std::size_t(child.N), "unseen edges leave the room apparently open");
    std::vector<std::uint64_t> messages;
    for (int i = 0; i < b.blocks(); ++i)
        if (masks[i]) messages.push_back(blockPacket(i, masks[i], child.round, b.context()));
    bot::Gossip g;
    g.hearWalls(child, b, messages);
    bot::Targets after;
    after.scanRegion(child);
    require(after.regionQueue.size() == std::size_t(P.shareKelpEdges ? 64 : child.N),
            "first inbox closes the room, while the disabled switch leaves unknown edges open");
    require(child.lastSeen[child.head] < 0, "a wall report does not invent tile sightings");
}

void sight_wins_and_rejects_invalid_messages() {
    auto w = test::world(15, 15);
    int const e = w.edgeOf(w.tileOf(12,12),0), block = 15;
    bot::Belief b;
    b.init(w);
    auto const valid = blockPacket(block, 1, w.round, b.context());
    bot::KelpKnowledge k;
    k.init(w);
    k.hear(w, {valid}, b.context());
    require(k.apply(w) == 0 && w.edgeKind[e] == bot::EDGE_OPEN, "seen open is authoritative");
    w.setEdge(e, bot::EDGE_PORTAL, 6);
    require(k.apply(w) == 0 && w.edgeKind[e] == bot::EDGE_PORTAL, "seen portal is authoritative");
    w.setEdge(e, bot::EDGE_UNKNOWN);
    require(k.apply(w) == 1 && w.step(e,0) == bot::KELP, "unknown becomes kelp and step cache changes");
    w.setEdge(e, bot::EDGE_OPEN);
    require(k.apply(w) == 0, "later direct sight overwrites the report");
    for (int kind = 0; kind < 6; ++kind) {
        k.init(w);
        auto ctx = b.context();
        int round = w.round;
        std::uint32_t mask = 1;
        int where = block;
        if (kind == 0) ctx.teamB = !ctx.teamB;
        if (kind == 1) ++round;
        if (kind == 2) round -= std::max(P.gossipMaxAge, P.kingReportAge) + 2;
        if (kind == 3) mask = 1U << 3; // x=15 lies beyond this partial block
        if (kind == 4) where = b.blocks();
        auto value = blockPacket(where, mask, round, ctx);
        if (kind == 5) value ^= 1ULL << 20;
        k.hear(w, {value}, b.context());
        bool const permitted = (kind == 0 && !P.sonarTeamTag) || (kind == 2 && !P.sonarDropStale);
        require(permitted ? k.seen[e] >= 0
                          : std::all_of(k.seen.begin(), k.seen.end(), [](int r) {return r < 0;}),
                "message validity follows the tag and stale switches; invalid blocks are rejected as a whole");
    }
}

void countdown_keeps_its_original_news() {
    auto w = test::world();
    bot::Belief b;
    b.init(w);
    bot::Record r;
    r.countdown = true;
    r.where = w.head;
    r.round = w.round;
    r.wait = P.gossipCountdownMax;
    r.symmetry = 2;
    bot::KelpKnowledge k;
    k.init(w);
    int const e = w.edgeOf(w.head, 0);
    k.edges = {e};
    k.seen[e] = w.round;
    auto const value = k.attach(w, r, b.context());
    auto const rec = bot::decodeRecord(value, b.context());
    require(rec && rec->countdown && rec->where == r.where && rec->wait == r.wait && rec->symmetry == r.symmetry,
            "wall bit does not displace countdown or symmetry");
    k.init(w);
    k.hear(w, {value}, b.context());
    w.setEdge(e, bot::EDGE_UNKNOWN);
    require(k.apply(w) == 1, "the same report also teaches a wall");
    // Kelp is permanent: a wall seen longer ago than any report lives still rides on a fresh countdown.
    k.init(w);
    k.seen[e] = w.round - std::max(P.gossipMaxAge, P.kingReportAge) - 2;
    k.edges = {e};
    auto const old = k.attach(w, r, b.context());
    require(old != bot::encodeRecord(r, b.context()), "an old wall still rides on the countdown");
    k.init(w);
    k.hear(w, {old}, b.context());
    w.setEdge(e, bot::EDGE_UNKNOWN);
    require(k.apply(w) == 1, "and the receiver accepts it at the countdown's round");
}

void split_routing_uses_post_split_body_and_portal_destinations() {
    auto w = test::world();
    test::body(w, {w.tileOf(8,8),w.tileOf(7,8),w.tileOf(6,8),w.tileOf(5,8)});
    w.facing[w.head] = w.facing[w.body[1]] = 1;
    require(bot::KelpKnowledge::childRay(w,2) == 3, "straight cut keeps the neck ray");
    w = test::world();
    test::body(w, {w.tileOf(8,8),w.tileOf(7,8),w.tileOf(7,9),w.tileOf(8,9)});
    w.facing[w.head] = w.facing[w.body[1]] = 1;
    test::wall(w, w.head, 0);
    test::wall(w, w.head, 1);
    w.setEdge(w.edgeOf(w.head,2), bot::EDGE_PORTAL, 7);
    require(bot::KelpKnowledge::childRay(w,2) < 0, "an unpaired portal cannot claim the child across its plain edge");
    w = test::world();
    test::body(w, {w.tileOf(8,8),w.tileOf(7,8),w.tileOf(3,3),w.tileOf(3,4)});
    w.facing[w.head] = w.facing[w.body[1]] = 1;
    test::wall(w,w.head,0);
    test::wall(w,w.head,1);
    w.setEdge(w.edgeOf(w.head,2),bot::EDGE_PORTAL,7);
    w.setEdge(w.edgeOf(w.tileOf(3,4),0),bot::EDGE_PORTAL,7);
    w.setEdge(w.edgeOf(w.body[1],2),bot::EDGE_PORTAL,8);
    w.setEdge(w.edgeOf(w.body[2],0),bot::EDGE_PORTAL,8);
    require(bot::KelpKnowledge::childRay(w,2) == 2, "paired portal lands on the child's known head");
    w.body.pop_back();
    require(bot::KelpKnowledge::childRay(w,2) < 0, "missing tail knowledge is not invented");
    w = test::world();
    test::body(w, {w.tileOf(8,8),w.tileOf(7,8),w.tileOf(7,7),w.tileOf(8,7),w.tileOf(9,7),w.tileOf(9,8)});
    w.facing[w.head] = w.facing[w.body[1]] = 1;
    w.facing[w.body[3]] = 3;
    require(bot::KelpKnowledge::childRay(w,2) == 3, "north hits the retained body; only neck refracts to the child");
}

void obstructed_or_unseen_rays_cannot_claim_delivery() {
    auto w = test::world();
    test::body(w, {w.tileOf(8,8),w.tileOf(7,8),w.tileOf(7,9),w.tileOf(8,9)});
    w.facing[w.head] = w.facing[w.body[1]] = 1;
    for (int d : {0,1}) test::wall(w,w.head,d);
    test::wall(w,w.body[1],3);
    w.setEdge(w.edgeOf(w.head,2),bot::EDGE_UNKNOWN);
    require(bot::KelpKnowledge::childRay(w,2) < 0, "unknown edge cannot prove the only direct path");
    w.setEdge(w.edgeOf(w.head,2),bot::EDGE_OPEN);
    require(bot::KelpKnowledge::childRay(w,2) == 2, "known open direct path works");
}


void portal_shortcuts_do_not_change_segment_facing() {
    auto w = test::world();
    test::body(w,{w.tileOf(8,8),w.tileOf(7,8),w.tileOf(6,8),w.tileOf(5,8),w.tileOf(4,8)});
    for (int t : w.body) w.facing[t] = 1;
    for (int d : {0,1,2}) test::wall(w,w.head,d);
    test::wall(w,w.body[2],0);
    // South from the retained tail also reaches its predecessor through a portal. Its actual facing is
    // east, so the neck ray must still leave west and hit the child.
    w.setEdge(w.edgeOf(w.body[2],2),bot::EDGE_PORTAL,9);
    w.setEdge(w.edgeOf(w.body[1],0),bot::EDGE_PORTAL,9);
    require(bot::KelpKnowledge::childRay(w,2) == 3, "observed tail facing wins over an alternative portal link");
    w.visStamp[w.body[2]] = 0;
    require(bot::KelpKnowledge::childRay(w,2) < 0, "an unseen tail with ambiguous links cannot claim refraction");
}

void only_the_local_closed_region_is_shared() {
    auto w = test::world();
    bot::KelpKnowledge k;
    k.init(w);
    int const unrelated = w.edgeOf(w.tileOf(1,1),0);
    test::wall(w,w.tileOf(1,1),0);
    k.seen[unrelated] = w.round;
    k.localEdges(w);
    require(k.edges.empty(), "an isolated wall on an otherwise connected map is not a closed region");
    for (int i=6;i<=10;++i) {
        test::wall(w,w.tileOf(i,6),0);
        test::wall(w,w.tileOf(i,10),2);
        test::wall(w,w.tileOf(6,i),3);
        test::wall(w,w.tileOf(10,i),1);
    }
    for (int e=0;e<2*w.N;++e)
        if (w.edgeKind[e]==bot::EDGE_KELP) k.seen[e]=w.round;
    k.localEdges(w);
    require(!k.edges.empty() && std::find(k.edges.begin(),k.edges.end(),unrelated)==k.edges.end(),
            "walls outside the sender's closed component are excluded");
    // A second sealed room, where a split child would start when the body crosses a portal.
    for (int i=12;i<=14;++i) {
        test::wall(w,w.tileOf(i,12),0);
        test::wall(w,w.tileOf(i,14),2);
        test::wall(w,w.tileOf(12,i),3);
        test::wall(w,w.tileOf(14,i),1);
    }
    int const childRoom = w.edgeOf(w.tileOf(13,12),0);
    for (int e=0;e<2*w.N;++e)
        if (w.edgeKind[e]==bot::EDGE_KELP) k.seen[e]=w.round;
    k.localEdges(w);
    require(std::find(k.edges.begin(),k.edges.end(),childRoom)==k.edges.end(), "another room is not ours");
    k.localEdges(w,w.tileOf(13,13));
    require(std::find(k.edges.begin(),k.edges.end(),childRoom)!=k.edges.end(),
            "on a split, the child's starting room is shared too");
}

// A walled room around a straight east-facing body; every wall was seen long before this round.
bot::World roomSplit(bot::Gossip& g, bot::Belief& b, int symmetry) {
    auto w = test::world();
    test::body(w, {w.tileOf(8,8),w.tileOf(7,8),w.tileOf(6,8),w.tileOf(5,8)});
    w.facing[w.head] = w.facing[w.body[1]] = 1;
    w.symmetry = symmetry;
    for (int i = 1; i <= 13; ++i) {
        test::wall(w,w.tileOf(i,1),0);
        test::wall(w,w.tileOf(i,13),2);
        test::wall(w,w.tileOf(1,i),3);
        test::wall(w,w.tileOf(13,i),1);
    }
    b.init(w);
    b.setSpawn(w.tileOf(10,10), w.round + 5, w.round);
    g.kelp.init(w);
    for (int e = 0; e < 2*w.N; ++e)
        if (w.edgeKind[e] == bot::EDGE_KELP)
            g.kelp.seen[e] = w.round - P.gossipMaxAge - 5;
    return w;
}

void wall_block_leaves_the_mirror_on_the_neck_ray() {
    bot::Gossip g;
    bot::Belief b;
    auto w = roomSplit(g, b, 0);
    int const neck = bot::neckDir(w);
    g.plan(w, b, w.head, neck, true, 2);
    auto const rec = bot::decodeRecord(g.out[neck], b.context());
    require(g.sending[neck] && rec && rec->countdown && (!P.shareSymmetry || rec->symmetry == 0),
            "the child's mirror countdown keeps the neck ray, the only one reaching the child");
    for (int d = 0; d < 4; ++d)
        require(!g.sending[d] || bot::decodeRecord(g.out[d], b.context()), "no other ray is proven, so no block");
    bot::Gossip plain;
    bot::Belief b2;
    auto u = roomSplit(plain, b2, -1);
    plain.plan(u, b2, u.head, neck, true, 2);
    require(plain.sending[neck] && !bot::decodeRecord(plain.out[neck], b2.context()) == P.shareKelpEdges,
            "without a mirror to deliver, the wall block takes the neck ray");
    auto child = u;
    for (int e = 0; e < 2*u.N; ++e)
        if (u.edgeKind[e] == bot::EDGE_KELP)
            child.setEdge(e, bot::EDGE_UNKNOWN);
    bot::KelpKnowledge k;
    k.init(child);
    k.hear(child, {plain.out[neck]}, b2.context());
    require((k.apply(child) > 0) == P.shareKelpEdges, "walls seen long ago still reach the child");
}

void split_packet_prioritizes_walls_the_child_cannot_see() {
    auto w = test::world();
    test::body(w,{w.tileOf(8,8),w.tileOf(7,8),w.tileOf(6,8),w.tileOf(5,8)});
    w.facing[w.head] = w.facing[w.body[1]] = 1;
    bot::Belief b;
    b.init(w);
    bot::KelpKnowledge k;
    k.init(w);
    int const near = w.edgeOf(w.body.back(),0), far = w.edgeOf(w.tileOf(5,2),0);
    k.edges={near}; k.seen[near]=w.round;
    require(!k.childPacket(w,2,b.context()), "known-at-birth walls do not displace ordinary news");
    k.edges.push_back(far); k.seen[far]=w.round;
    auto const packet=k.childPacket(w,2,b.context());
    require(packet && packet->first==3, "useful wall gets the child's ray");
    auto child=w;
    child.setEdge(far,bot::EDGE_UNKNOWN);
    k.init(child);
    k.hear(child,{packet->second},b.context());
    require(k.apply(child)==1 && child.edgeKind[far]==bot::EDGE_KELP, "block carries the wall outside the first view");
}

void wall_blocks_and_census_records_stay_apart() {
    auto w = test::world();
    test::body(w,{w.tileOf(8,8),w.tileOf(7,8),w.tileOf(6,8),w.tileOf(5,8)});
    w.facing[w.head] = w.facing[w.body[1]] = 1;
    bot::Belief b;
    b.init(w);
    bot::Record census;
    census.census = true;
    census.round = census.cen.round = w.round;
    census.cen.turns = 1;
    auto const wire = bot::encodeRecord(census, b.context());
    auto const heard = bot::decodeRecord(wire, b.context());
    require(heard && heard->census, "a census record carries bits 0, 31 and 50");
    auto blind = w;
    blind.setAllEdges(bot::EDGE_UNKNOWN);
    bot::KelpKnowledge k;
    k.init(blind);
    k.hear(blind, {wire}, b.context());
    require(k.apply(blind) == 0, "a census record adds no walls");
    k.init(w);
    int const slot13 = w.tileOf(5,3), far = w.edgeOf(w.tileOf(5,2),0);
    k.edges={slot13}; k.seen[slot13]=w.round;
    require(!k.childPacket(w,2,b.context()), "the census slot's wall alone makes no block");
    k.edges.push_back(far); k.seen[far]=w.round;
    auto const packet=k.childPacket(w,2,b.context());
    require(packet && !(packet->second >> 31 & 1) && !bot::decodeRecord(packet->second, b.context()),
            "a block packet never reads as a census or king record");
    auto child=w;
    child.setEdge(far,bot::EDGE_UNKNOWN);
    child.setEdge(slot13,bot::EDGE_UNKNOWN);
    k.init(child);
    k.hear(child,{packet->second},b.context());
    require(k.apply(child)==1 && child.edgeKind[far]==bot::EDGE_KELP, "the block still carries its other walls");
}

} // namespace

int main() {
    return test::suite({
        {"sealed room is known from birth",sealed_room_is_known_from_birth},
        {"sight wins and rejects invalid messages",sight_wins_and_rejects_invalid_messages},
        {"countdown keeps its original news",countdown_keeps_its_original_news},
        {"split routing uses post-split body and portal destinations",split_routing_uses_post_split_body_and_portal_destinations},
        {"obstructed or unseen rays cannot claim delivery",obstructed_or_unseen_rays_cannot_claim_delivery},
        {"portal shortcuts do not change segment facing",portal_shortcuts_do_not_change_segment_facing},
        {"only the local closed region is shared",only_the_local_closed_region_is_shared},
        {"split packet prioritizes walls the child cannot see",split_packet_prioritizes_walls_the_child_cannot_see},
        {"wall block leaves the mirror on the neck ray",wall_block_leaves_the_mirror_on_the_neck_ray},
        {"wall blocks and census records stay apart",wall_blocks_and_census_records_stay_apart},
    });
}
