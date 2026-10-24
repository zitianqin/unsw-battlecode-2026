#pragma once

#include "belief.hpp"
#include "state.hpp"

#include <cstdint>
#include <optional>
#include <vector>

namespace bot {

// Pearl claims (P.pearlClaims). Two of our dragons out of each other's sight often walk to the same pearl (a fifth of
// pearl-target turns on Trauma share a target, seven in ten of those between dragons that can't see each other), and
// someoneElseFirst (targets.hpp) yields only to a teammate in view. So each dragon names its pearl target and the round
// it expects to eat it on a sonar ray, ranked against gossip's records (gossip.hpp's gather), and a dragon that hears a fresh claim
// on a pearl yields it when the claimant eats it first (ties to the lower id), as it would to a teammate in view.
// A claim holds until its eat round passes; claims aren't relayed.
//
// On the wire a claim is a view-kind body (bit 0 clear) whose seen mask (bits 18..33) is empty, which decodeRecord
// rejects and the other decoders (kelp.hpp, doorway.hpp) skip as a view: the round the claimant expects to eat the
// pearl in 1..9, the target tile's low 8 bits in 10..17 and high 4 in 34..37, and the claimant's id's low 13 bits in
// 38..50.
struct PearlClaim {
    int eat = 0;  // the round the claimant expects to eat the pearl
    int tile = 0;
    int id = 0;   // low ID_BITS bits of the claimant's id
    static constexpr int ID_BITS = 13;
    static constexpr int ID_MASK = (1 << ID_BITS) - 1;
};

inline std::uint64_t encodeClaim(PearlClaim const& c, wire::Context const& ctx) {
    auto const tile = static_cast<std::uint64_t>(c.tile & 4095);
    std::uint64_t const body = wire::field(c.eat, 9) << 1 | (tile & 255) << 10 | (tile >> 8) << 34 |
                               static_cast<std::uint64_t>(c.id & PearlClaim::ID_MASK) << 38;
    return body | wire::tagOf(body, ctx) << wire::BODY_BITS;
}

// The claim in `value`, or nothing when it is another kind or fails our tag.
inline std::optional<PearlClaim> decodeClaim(std::uint64_t value, wire::Context const& ctx) {
    std::uint64_t const body = value & wire::BODY_MASK;
    if ((body & 1) || (body >> 18 & 0xFFFF) || value >> wire::BODY_BITS != wire::tagOf(body, ctx))
        return std::nullopt;
    PearlClaim c;
    c.eat = static_cast<int>(body >> 1 & 511);
    c.tile = static_cast<int>((body >> 10 & 255) | (body >> 34 & 15) << 8);
    c.id = static_cast<int>(body >> 38 & PearlClaim::ID_MASK);
    return c;
}

// The claims this dragon heard: for each tile the one that eats it first. Call hear() each turn with its messages.
struct Claims {
    std::vector<int> eat; // tile -> round the best claim on it eats the pearl, -1 none
    std::vector<int> by;  // tile -> that claimant's id (its low PearlClaim::ID_BITS bits)

    void hear(World const& w, wire::Context const& ctx, std::vector<std::uint64_t> const& messages) {
        if (eat.empty()) {
            eat.assign(w.N, -1);
            by.assign(w.N, -1);
        }
        for (auto value : messages) {
            auto const c = decodeClaim(value, ctx);
            if (!c || c->tile >= w.N || c->id == (w.myId & PearlClaim::ID_MASK) || c->eat < w.round)
                continue;
            if (eat[c->tile] < w.round || c->eat < eat[c->tile] || (c->eat == eat[c->tile] && c->id < by[c->tile])) {
                eat[c->tile] = c->eat;
                by[c->tile] = c->id;
            }
        }
    }

    // True when a fresh claim on `tile` eats it before we would, `myEat` steps from now (ties to the lower id).
    bool beaten(World const& w, int tile, int myEat) const {
        if (eat.empty() || eat[tile] < w.round)
            return false;
        int const mine = w.round + myEat;
        return eat[tile] < mine || (eat[tile] == mine && by[tile] < (w.myId & PearlClaim::ID_MASK));
    }
};

} // namespace bot
