#pragma once

#include "params.hpp"

#include <cstddef>
#include <cstdint>

// The secret behind our sonar tag (belief.hpp's wire::tagOf). With P.sonarBuildKey it is a hash of this bot's own
// source, taken at compile time with #embed: every dragon of one binary shares it, and any other build (the
// champion, an old version, a rival that harvested our values from replays) has another. The judge rejects
// __DATE__ and __TIME__ (-Werror=date-time), and identical source gives the identical key, so games stay
// reproducible. A compiler without #embed falls back to the fixed key.
namespace bot::wire {

inline constexpr std::uint64_t KEY = 0x3f6a2c8e91d4b705ULL;

// splitmix64's finaliser: every input bit changes every output bit.
constexpr std::uint64_t mix(std::uint64_t z) {
    z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9ULL;
    z = (z ^ (z >> 27)) * 0x94d049bb133111ebULL;
    return z ^ (z >> 31);
}

// FNV-1a of n bytes, continuing from h.
consteval std::uint64_t fnv(std::uint64_t h, unsigned char const* p, std::size_t n) {
    for (std::size_t i = 0; i < n; i++)
        h = (h ^ p[i]) * 0x100000001b3ULL;
    return h;
}

#if defined(__has_embed)
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wc23-extensions"
// Every source file of the bot; a new file must be added here (or changes only it would keep the old key). The
// listener's generated table (listen_formats.hpp) is left out: g++ stops a constant-evaluated loop at 262,144 steps,
// fewer than its bytes, and a build that changes only table rows keeps the key.
inline constexpr unsigned char SRC_MAIN[] = {
#embed "main.cpp"
};
inline constexpr unsigned char SRC_BELIEF[] = {
#embed "belief.hpp"
};
inline constexpr unsigned char SRC_CENSUS[] = {
#embed "census.hpp"
};
inline constexpr unsigned char SRC_DOORWAY[] = {
#embed "doorway.hpp"
};
inline constexpr unsigned char SRC_ESCAPE[] = {
#embed "escape.hpp"
};
inline constexpr unsigned char SRC_FARM[] = {
#embed "farm.hpp"
};
inline constexpr unsigned char SRC_FEED[] = {
#embed "feed.hpp"
};
inline constexpr unsigned char SRC_FOLD[] = {
#embed "fold.hpp"
};
inline constexpr unsigned char SRC_GOSSIP[] = {
#embed "gossip.hpp"
};
inline constexpr unsigned char SRC_KELP[] = {
#embed "kelp.hpp"
};
inline constexpr unsigned char SRC_HELPER[] = {
#embed "helper.hpp"
};
inline constexpr unsigned char SRC_HUNT[] = {
#embed "hunt.hpp"
};
inline constexpr unsigned char SRC_LISTEN[] = {
#embed "listen.hpp"
};
inline constexpr unsigned char SRC_SPOOF[] = {
#embed "spoof.hpp"
};
inline constexpr unsigned char SRC_PARAMS[] = {
#embed "params.hpp"
};
inline constexpr unsigned char SRC_RAM[] = {
#embed "ram.hpp"
};
inline constexpr unsigned char SRC_ROOMSPLIT[] = {
#embed "roomsplit.hpp"
};
inline constexpr unsigned char SRC_SAFETY[] = {
#embed "safety.hpp"
};
inline constexpr unsigned char SRC_SCORING[] = {
#embed "scoring.hpp"
};
inline constexpr unsigned char SRC_SHED[] = {
#embed "shed.hpp"
};
inline constexpr unsigned char SRC_SONARKEY[] = {
#embed "sonarkey.hpp"
};
inline constexpr unsigned char SRC_SPLIT[] = {
#embed "split.hpp"
};
inline constexpr unsigned char SRC_STATE[] = {
#embed "state.hpp"
};
inline constexpr unsigned char SRC_TARGETS[] = {
#embed "targets.hpp"
};
#pragma clang diagnostic pop

// Hash each source once at compile time; adding a module here also covers changes confined to that header.
inline constexpr std::uint64_t SOURCE_HASH = [] {
    std::uint64_t h = 0xcbf29ce484222325ULL;
    h = fnv(h, SRC_MAIN, sizeof SRC_MAIN);
    h = fnv(h, SRC_BELIEF, sizeof SRC_BELIEF);
    h = fnv(h, SRC_CENSUS, sizeof SRC_CENSUS);
    h = fnv(h, SRC_DOORWAY, sizeof SRC_DOORWAY);
    h = fnv(h, SRC_ESCAPE, sizeof SRC_ESCAPE);
    h = fnv(h, SRC_FARM, sizeof SRC_FARM);
    h = fnv(h, SRC_FEED, sizeof SRC_FEED);
    h = fnv(h, SRC_FOLD, sizeof SRC_FOLD);
    h = fnv(h, SRC_GOSSIP, sizeof SRC_GOSSIP);
    h = fnv(h, SRC_KELP, sizeof SRC_KELP);
    h = fnv(h, SRC_HELPER, sizeof SRC_HELPER);
    h = fnv(h, SRC_HUNT, sizeof SRC_HUNT);
    h = fnv(h, SRC_LISTEN, sizeof SRC_LISTEN);
    h = fnv(h, SRC_SPOOF, sizeof SRC_SPOOF);
    h = fnv(h, SRC_PARAMS, sizeof SRC_PARAMS);
    h = fnv(h, SRC_RAM, sizeof SRC_RAM);
    h = fnv(h, SRC_ROOMSPLIT, sizeof SRC_ROOMSPLIT);
    h = fnv(h, SRC_SAFETY, sizeof SRC_SAFETY);
    h = fnv(h, SRC_SCORING, sizeof SRC_SCORING);
    h = fnv(h, SRC_SHED, sizeof SRC_SHED);
    h = fnv(h, SRC_SONARKEY, sizeof SRC_SONARKEY);
    h = fnv(h, SRC_SPLIT, sizeof SRC_SPLIT);
    h = fnv(h, SRC_STATE, sizeof SRC_STATE);
    return fnv(h, SRC_TARGETS, sizeof SRC_TARGETS);
}();
inline constexpr bool HAS_BUILD_KEY = true;
#else
inline constexpr std::uint64_t SOURCE_HASH = 0;
inline constexpr bool HAS_BUILD_KEY = false;
#endif

// The two halves of the 128-bit SipHash key.
inline constexpr std::uint64_t KEY0 = P.sonarBuildKey && HAS_BUILD_KEY ? mix(SOURCE_HASH ^ KEY) : KEY;
inline constexpr std::uint64_t KEY1 = mix(KEY0 ^ 0x9e3779b97f4a7c15ULL);

constexpr std::uint64_t rotl(std::uint64_t x, int b) { return x << b | x >> (64 - b); }

// SipHash-2-4 (Aumasson and Bernstein) of the 16 bytes m0, m1 (little-endian words) under key k0, k1.
constexpr std::uint64_t sipHash(std::uint64_t k0, std::uint64_t k1, std::uint64_t m0, std::uint64_t m1) {
    std::uint64_t v0 = k0 ^ 0x736f6d6570736575ULL, v1 = k1 ^ 0x646f72616e646f6dULL;
    std::uint64_t v2 = k0 ^ 0x6c7967656e657261ULL, v3 = k1 ^ 0x7465646279746573ULL;
    auto const round = [&] {
        v0 += v1;
        v1 = rotl(v1, 13) ^ v0;
        v0 = rotl(v0, 32);
        v2 += v3;
        v3 = rotl(v3, 16) ^ v2;
        v0 += v3;
        v3 = rotl(v3, 21) ^ v0;
        v2 += v1;
        v1 = rotl(v1, 17) ^ v2;
        v2 = rotl(v2, 32);
    };
    std::uint64_t const blocks[3] = {m0, m1, 16ULL << 56}; // the last block is the length byte alone
    for (std::uint64_t const m : blocks) {
        v3 ^= m;
        round();
        round();
        v0 ^= m;
    }
    v2 ^= 0xff;
    for (int i = 0; i < 4; i++)
        round();
    return v0 ^ v1 ^ v2 ^ v3;
}

} // namespace bot::wire
