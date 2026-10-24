#pragma once

#include "belief.hpp"
#include "params.hpp"
#include "state.hpp"
#include "targets.hpp"

#include <array>
#include <cstdint>
#include <optional>
#include <vector>

namespace bot {

// Doorway probes (P.doorwayProbe). Vision stops at a portal, so a dragon crossing one lands blind, and a teammate's
// head on the landing tile kills both. Sonar passes portals and stops on the first dragon part it meets, so a dragon
// whose head ends its turn on a tile with a portal edge sends a probe through that edge: it lands on whoever stands on
// the partner mouth, the tile a crossing back through the pair lands on. A dragon that hears a probe for the portal
// edge beside its head knows a teammate's head is where that crossing lands, and scores it as a head-on with a
// teammate (World::doorProbed, evaluateMove). Sonar is read at the recipient's next turn, so every probe heard was sent
// after the sender's latest move: by a higher id that ended the last round there (it hasn't moved since) or a lower id
// that stepped there this round. A probe sent before a teammate reaches the partner mouth hits nothing there, and
// every dragon moves each turn, so of two crossers arriving at the two mouths only one hears the other: it steps off
// and leaves its neck on the other's landing tile, a body the other can still cross into (body landings are out of
// this probe's scope).
//
// On the wire a probe is a countdown-kind body (bit 0 set, bit 31 clear) with bit 48 set, which decodeRecord and
// KelpKnowledge::hear both drop: round in 1..9, the sender's head tile in 10..21, the ray's direction in 22..23 and
// the portal id's low 23 bits in 24..30 and 32..47; bits 49 and 50 clear. The tag (belief.hpp) keeps enemies out.
struct DoorProbe {
    int round = 0;
    int tile = 0;   // the sender's head
    int dir = 0;    // the way its ray left, through the portal edge on that side of its head
    int portal = 0; // that edge's portal id, low PORTAL_BITS bits
    static constexpr int PORTAL_BITS = 23;
    static constexpr int PORTAL_MASK = (1 << PORTAL_BITS) - 1;
};

inline std::uint64_t encodeProbe(DoorProbe const& p, wire::Context const& ctx) {
    auto const id = static_cast<std::uint64_t>(p.portal & DoorProbe::PORTAL_MASK);
    std::uint64_t const body = 1 | wire::field(p.round, 9) << 1 | wire::field(p.tile, 12) << 10 |
                               wire::field(p.dir, 2) << 22 | (id & 0x7F) << 24 | (id >> 7) << 32 | 1ULL << 48;
    return body | wire::tagOf(body, ctx) << wire::BODY_BITS;
}

// The probe in `value`, or nothing when it is another kind or fails our tag.
inline std::optional<DoorProbe> decodeProbe(std::uint64_t value, wire::Context const& ctx) {
    std::uint64_t const body = value & wire::BODY_MASK;
    if (!(body & 1) || (body >> 31 & 1) || (body >> 48) != 1 || value >> wire::BODY_BITS != wire::tagOf(body, ctx))
        return std::nullopt;
    DoorProbe p;
    p.round = static_cast<int>(body >> 1 & 511);
    p.tile = static_cast<int>(body >> 10 & 4095);
    p.dir = static_cast<int>(body >> 22 & 3);
    p.portal = static_cast<int>((body >> 24 & 0x7F) | (body >> 32 & 0xFFFF) << 7);
    return p;
}

// The probes to send this turn by direction, from `origin` (our head after the action): one through each portal edge
// on its sides, except `tailDir`, the side opposite our facing, whose ray leaves through our tail instead.
inline std::array<std::optional<std::uint64_t>, 4> doorProbes(World const& w, wire::Context const& ctx, int origin,
                                                               int tailDir) {
    std::array<std::optional<std::uint64_t>, 4> out{};
    if (!P.doorwayProbe || origin < 0)
        return out;
    for (int dir = 0; dir < 4; dir++) {
        int const e = w.edgeOf(origin, dir);
        if (dir != tailDir && w.edgeKind[e] == EDGE_PORTAL)
            out[dir] = encodeProbe({.round = w.round, .tile = origin, .dir = dir, .portal = w.edgePortal[e]}, ctx);
    }
    return out;
}

// Sets w.doorProbed from this turn's messages: side d is set when a probe came through the portal edge on that side
// of our head from its partner mouth, the tile our crossing lands on. The probe's portal id and direction name that
// mouth even while we don't know the pair: the same id, a ray heading the other way, and not our own edge (a sender
// across our own edge sends toward the far side). When we know the pair, its landing tile must be the sender's head.
inline void hearProbes(World& w, wire::Context const& ctx, std::vector<std::uint64_t> const& messages) {
    w.doorProbed.fill(false);
    if (!P.doorwayProbe)
        return;
    for (std::uint64_t const value : messages) {
        auto const p = decodeProbe(value, ctx);
        if (!p || p->round > w.round || p->round < w.round - 1 || p->tile >= w.N || p->tile == w.head)
            continue;
        for (int dir = 0; dir < 4; dir++) {
            int const e = w.edgeOf(w.head, dir);
            if (w.edgeKind[e] != EDGE_PORTAL || (w.edgePortal[e] & DoorProbe::PORTAL_MASK) != p->portal ||
                p->dir != (dir + 2) % 4 || w.edgeOf(p->tile, p->dir) == e)
                continue;
            int const lands = w.step(w.head, dir);
            if (lands >= 0 && lands != p->tile)
                continue;
            w.doorProbed[dir] = true;
        }
    }
}

// Keeping portal doorways clear (P.keepDoorways). A doorway is a tile with a known portal edge on one of its sides,
// paired or not: it is where every crossing through that edge's partner lands, and vision stops at a portal, so a
// teammate crossing from the far side can't see us there and dies on our head or body. evaluateMoves charges a move
// that ends on a doorway doorwayCharge() for the rounds our body will cover the tile, as a tie-break: only while
// another move without a survival penalty makes as much progress towards our target and already outscores every
// move that makes less (scoring.hpp's doorwayCharges), so a dragon never walks away from its target to dodge a
// doorway (it would be back beside it next turn) and still takes a doorway that is its only good move.

// Whether tile t has a known portal edge on any side.
inline bool isDoorway(World const& w, int t) {
    for (int d = 0; d < 4; d++)
        if (w.edgeKind[w.edgeOf(t, d)] == EDGE_PORTAL)
            return true;
    return false;
}

// The path field evaluateMove scores progress by this turn: the pearl target's, else the explore target's, else null.
inline DistField const* progressField(Targets const& targets) {
    if (targets.pearl >= 0)
        return &targets.pearlField;
    return targets.explore >= 0 ? &targets.exploreField : nullptr;
}

// Whether our move after landing on doorway `dest` crosses one of its portals on our way: into the unknown portal
// our explore target sits beside, or through a paired one to a tile nearer the target by its path field.
inline bool crossesNext(World const& w, Targets const& targets, int dest) {
    DistField const* field = progressField(targets);
    for (int d = 0; d < 4; d++) {
        if (w.edgeKind[w.edgeOf(dest, d)] != EDGE_PORTAL)
            continue;
        int const to = w.step(dest, d);
        if (to == UNKNOWN_PORTAL) {
            if (targets.pearl < 0 && targets.explore == dest && targets.explorePortalDir == d)
                return true;
        } else if (to >= 0 && field && field->at(to) < field->at(dest)) {
            return true;
        }
    }
    return false;
}

// What ending this turn's move `dir` on its destination `dest` costs for blocking a doorway: P.doorwayCost steps of
// P.targetWeight for each round our body covers the tile (our length: every dragon moves every round), or 0 when it
// is no doorway, or we cross a portal to get there (a crossing always lands on one; charging it would cut the
// crossings, not the loitering), eat a pearl there, it is our pearl target, or our next move crosses its portal.
inline double doorwayCharge(World const& w, Targets const& targets, int dir, int dest) {
    if (dest < 0 || !isDoorway(w, dest) || w.edgeKind[w.edgeOf(w.head, dir)] == EDGE_PORTAL)
        return 0;
    if (w.hasPearlHere(dest) || dest == targets.pearl || crossesNext(w, targets, dest))
        return 0;
    return P.doorwayCost * P.targetWeight * w.length;
}

} // namespace bot
