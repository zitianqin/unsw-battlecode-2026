#pragma once

#include "belief.hpp"
#include <bit>
#include <optional>
#include <utility>

namespace bot {

// Shares only kelp, never inferred open edges or portal identities. A block packet uses bit 50 plus bit 0
// as its kind, round in 1..9, block in 10..17 and north/west wall masks in 18..49, with bit 31 always clear:
// bits 0, 31 and 50 together mark a census record (belief.hpp), so the mask's slot at bit 31 (MASK_CENSUS_SLOT)
// never goes in a block and that edge travels only on countdowns. Countdown reports keep their original fields
// and carry one canonical edge in 34..46, with presence in 47 (48..50 remain clear).
struct KelpKnowledge {
    static constexpr int MASK_CENSUS_SLOT = 31 - 18;

    std::vector<int> seen, sent;
    std::vector<int> region, queue, edges;
    int stamp = 0;
    int blocksWide = 0;

    void init(World const& w) {
        seen.assign(2 * w.N, -1);
        sent.assign(2 * w.N, -1);
        region.assign(w.N, 0);
        blocksWide = (w.W + BLOCK - 1) / BLOCK;
    }

    void observe(World const& w) {
        for (int t : w.touched)
            if (w.visible(t))
                for (int d = 0; d < 4; ++d) {
                    int const e = w.edgeOf(t, d);
                    if (w.edgeKind[e] == EDGE_KELP)
                        seen[e] = w.round;
                    else
                        seen[e] = -1;
                }
    }

    void hear(World const& w, std::vector<std::uint64_t> const& messages, wire::Context const& ctx) {
        for (auto value : messages) {
            auto const body = value & wire::BODY_MASK;
            if (!(body & 1) || value >> wire::BODY_BITS != wire::tagOf(body, ctx))
                continue;
            int const r = (body >> 1) & 511;
            if (r > w.round || (P.sonarDropStale && w.round - r > std::max(P.gossipMaxAge, P.kingReportAge) + 1))
                continue;
            if (body >> 50) {
                if (body >> 31 & 1)
                    continue;
                int const block = (body >> 10) & 255;
                int const bx = block % blocksWide * BLOCK, by = block / blocksWide * BLOCK;
                std::uint32_t const mask = body >> 18;
                bool valid = by < w.H && mask != 0;
                for (int i = 0; valid && i < 32; ++i)
                    if ((mask >> i & 1) && (bx + i % 16 % BLOCK >= w.W || by + i % 16 / BLOCK >= w.H))
                        valid = false;
                if (!valid)
                    continue;
                for (int i = 0; i < 32; ++i)
                    if (mask >> i & 1) {
                        int const t = w.tileOf(bx + i % 16 % BLOCK, by + i % 16 / BLOCK);
                        int const e = t + (i / 16) * w.N;
                        seen[e] = std::max(seen[e], r);
                    }
            } else if (!(body >> 31 & 1) && body >> 47 & 1 && !(body >> 48) &&
                       static_cast<int>((body >> 10) & 4095) < w.N) {
                int const e = (body >> 34) & 8191;
                if (e < 2 * w.N)
                    seen[e] = std::max(seen[e], r);
            }
        }
    }

    int apply(World& w) const {
        int count = 0;
        for (int e = 0; e < 2 * w.N; ++e)
            if (seen[e] >= 0 && w.edgeKind[e] == EDGE_UNKNOWN) {
                w.setEdge(e, EDGE_KELP);
                ++count;
            }
        return count;
    }

    // Walls bordering the sender's current component, with portals as boundaries and unseen edges open; on a
    // split turn `childHead` (the old tail) also seeds it, so a body lying across a portal shares the child's
    // room too. Kelp never changes, so every wall known by sight or report counts, however old.
    // A component reaching the entire map has no known closed region to share.
    void localEdges(World const& w, int childHead = -1) {
        ++stamp;
        queue.clear();
        edges.clear();
        for (int seed : {w.head, childHead})
            if (seed >= 0 && region[seed] != stamp) {
                region[seed] = stamp;
                queue.push_back(seed);
            }
        for (std::size_t i = 0; i < queue.size(); ++i)
            for (int d = 0; d < 4; ++d) {
                int const t = queue[i], e = w.edgeOf(t, d);
                if (w.edgeKind[e] == EDGE_KELP || w.edgeKind[e] == EDGE_PORTAL)
                    continue;
                int const to = w.neighbour(t, d);
                if (region[to] != stamp) {
                    region[to] = stamp;
                    queue.push_back(to);
                }
            }
        if (static_cast<int>(queue.size()) == w.N)
            return;
        for (int e = 0; e < 2 * w.N; ++e) {
            int const t = e % w.N, d = e < w.N ? 0 : 3;
            if (seen[e] >= 0 && w.edgeKind[e] == EDGE_KELP &&
                (region[t] == stamp || region[w.neighbour(t, d)] == stamp))
                edges.push_back(e);
        }
    }

    // Traces the post-split rays. Only the initial neck direction refracts through the retained parent's
    // tail; a later collision with its body stops the ray. Portals move the ray to their paired edge,
    // preserving its compass direction, as World::step does.
    // Observed segment facing decides refraction; an unseen tail needs a unique known link to its predecessor.
    // Unknown edges cannot prove delivery. Prefer the neck ray, then compass order, never `avoid` or `avoid2` (rays
    // already carrying other news for the child).
    static int childRay(World const& w, int child, int avoid = -1, int avoid2 = -1) {
        if (static_cast<int>(w.body.size()) != w.length || child < unswbc::Constants::MIN_SIZE)
            return -1;
        int const kept = w.length - child;
        if (kept < unswbc::Constants::MIN_SIZE)
            return -1;
        int const tail = w.body[kept - 1];
        int tailDir = -1;
        if (w.visible(tail)) {
            tailDir = (w.facing[tail] + 2) % 4;
        } else {
            int toward = -1;
            for (int d = 0; d < 4; ++d)
                if (w.step(tail, d) == w.body[kept - 2]) {
                    if (toward >= 0) {
                        toward = -1;
                        break;
                    }
                    toward = d;
                }
            if (toward >= 0)
                tailDir = (toward + 2) % 4;
        }
        int const neck = (w.facing[w.head] + 2) % 4;
        for (int order = -1; order < 4; ++order) {
            int const start = order < 0 ? neck : order;
            if (start == avoid || start == avoid2 || (order >= 0 && start == neck))
                continue;
            int at = w.head, d = start;
            if (start == neck) {
                if (tailDir < 0)
                    continue;
                at = w.body[kept - 1];
                d = tailDir;
            }
            for (int step = 0; step < w.W + w.H; ++step) {
                int const e = w.edgeOf(at, d);
                if (w.edgeKind[e] == EDGE_UNKNOWN || w.edgeKind[e] == EDGE_KELP)
                    break;
                at = w.step(at, d);
                if (at < 0)
                    break;
                auto const own = std::find(w.body.begin(), w.body.end(), at);
                if (own != w.body.end()) {
                    if (own - w.body.begin() >= kept)
                        return start;
                    break;
                }
                if (!w.visible(at) || w.occId[at] >= 0)
                    break;
            }
        }
        return -1;
    }

    // Add one kelp edge, the one sent longest ago, without changing a countdown's existing fields. The wall
    // rides on the countdown's round; kelp is permanent, so any known wall may.
    std::uint64_t attach(World const& w, Record const& rec, wire::Context const& ctx) {
        auto const ordinary = encodeRecord(rec, ctx);
        if (!rec.countdown)
            return ordinary;
        int best = -1;
        for (int e : edges)
            if (best < 0 || sent[e] < sent[best])
                best = e;
        if (best < 0)
            return ordinary;
        auto const body = (ordinary & wire::BODY_MASK) | static_cast<std::uint64_t>(best) << 34 | 1ULL << 47;
        sent[best] = w.round;
        return body | wire::tagOf(body, ctx) << wire::BODY_BITS;
    }

    // A child's first view already sees either incident tile's edges. Count only walls it cannot see when
    // choosing a block; no useful new wall means no displacement of the ray's ordinary report.
    static bool childSeesEdge(World const& w, int head, int e) {
        auto const sees = [&](int t) {
            int const dx = std::abs(w.xOf(t) - w.xOf(head)), dy = std::abs(w.yOf(t) - w.yOf(head));
            return std::min(dx, w.W - dx) <= unswbc::Constants::VISION_RADIUS &&
                   std::min(dy, w.H - dy) <= unswbc::Constants::VISION_RADIUS;
        };
        int const t = e % w.N;
        return sees(t) || sees(w.neighbour(t, e < w.N ? 0 : 3));
    }

    // One block on a ray known to reach the split child, other than `avoid` (the ray carrying the child's
    // mirror countdown). Prefer the block carrying the most walls outside its first view. It is stamped with
    // this round: kelp is permanent, so a relayed wall is as true as a fresh one.
    std::optional<std::pair<int, std::uint64_t>> childPacket(World const& w, int child, wire::Context const& ctx,
                                                             int avoid = -1, int avoid2 = -1) const {
        if (!child || edges.empty())
            return std::nullopt;
        int const ray = childRay(w, child, avoid, avoid2);
        if (ray < 0)
            return std::nullopt;
        int const blocks = blocksWide * ((w.H + BLOCK - 1) / BLOCK);
        std::vector<std::uint32_t> masks(blocks, 0);
        std::vector<int> useful(blocks, 0);
        for (int e : edges) {
            int const t = e % w.N, x = w.xOf(t), y = w.yOf(t);
            int const block = y / BLOCK * blocksWide + x / BLOCK, slot = (e / w.N) * 16 + y % BLOCK * BLOCK + x % BLOCK;
            if (slot == MASK_CENSUS_SLOT)
                continue;
            masks[block] |= 1U << slot;
            useful[block] += !childSeesEdge(w, w.body.back(), e);
        }
        int best = -1;
        for (int block = 0; block < blocks; ++block)
            if (useful[block] && (best < 0 || useful[block] > useful[best]))
                best = block;
        if (best < 0)
            return std::nullopt;
        auto const body = 1ULL | static_cast<std::uint64_t>(w.round) << 1 |
                          static_cast<std::uint64_t>(best) << 10 | static_cast<std::uint64_t>(masks[best]) << 18 |
                          1ULL << 50;
        return std::pair{ray, body | wire::tagOf(body, ctx) << wire::BODY_BITS};
    }
};

} // namespace bot
