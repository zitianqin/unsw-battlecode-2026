#pragma once

#include "params.hpp"
#include "sonarkey.hpp"
#include "state.hpp"

#include <algorithm>
#include <array>
#include <bit>
#include <cmath>
#include <cstdint>
#include <deque>
#include <optional>
#include <tuple>
#include <utility>
#include <vector>

namespace bot {

// The census of the opponent: what our dragons saw of the enemy's heads, pooled over sonar so every dragon can read
// the opponent's size and style (a swarm, a lone king, a team folded down to a few dragons). Each process counts its
// own sightings over the last P.censusWindow rounds (Census) and keeps the team's census (CensusTable, merged by
// belief.hpp from records gossip.hpp relays): every sender's newest counts, and the union of the enemy ids any of us
// saw, which merges by OR so one record carries everything its sender knows. CensusTable::estimate reads the team's
// estimate from them. No move reads it yet.

// Wire widths of a census record (belief.hpp's encodeRecord): a counts record's sender is its dragon id modulo 2^12
// (ids count up across both teams, and a game makes far fewer than 4096 dragons); a union record carries its epoch
// modulo 32, a 7-bit hashed set of the dragons that saw its enemies and a 26-bit hashed set of those enemies' ids.
inline constexpr int CENSUS_SENDER_BITS = 12;
inline constexpr int CENSUS_EPOCH_BITS = 5;
inline constexpr int CENSUS_FROM_BITS = 7;
inline constexpr int CENSUS_SKETCH_BITS = 26;
inline constexpr int CENSUS_COUNT_MAX = 127;  // heads and shorts saturate here (7 bits) ...
inline constexpr int CENSUS_LONGEST_MAX = 63; // ... and the longest here (6 bits)
static_assert(P.censusWindow >= 2 && P.censusWindow <= 63, "a census record's turns field has 6 bits");

// Enemy ids are pooled by epochs of half the window: the union of the current epoch and the one before covers from
// half the window to all of it.
inline int censusEpoch(int round) { return round / (P.censusWindow / 2); }

// The epoch a record sent at `round` means by `epochBits` (its epoch modulo 2^CENSUS_EPOCH_BITS): that round's own
// epoch or the one before (a union we still send), else -1.
inline int censusEpochOf(int epochBits, int round) {
    int const e = censusEpoch(round);
    int const back = (e - epochBits) & ((1 << CENSUS_EPOCH_BITS) - 1);
    return back <= 1 && e - back >= 0 ? e - back : -1;
}

// A census record. Counts (`ids` false): one dragon's own turns over the P.censusWindow rounds up to `round`, the
// enemy heads it saw summed over them, those at most P.censusShortLen visibly long, and the longest visible enemy
// head. Union (`ids` true): the enemy ids the sender knows any of us saw in `epoch` (bit censusBit(id) of `sketch`)
// and who saw them (bit censusBit(dragon id, CENSUS_FROM_BITS) of `from`), as known at `round`.
struct CensusRecord {
    bool ids = false;
    int round = 0;
    int sender = 0;
    int turns = 0, heads = 0, shorts = 0, longest = 0;
    int epoch = 0;
    std::uint32_t from = 0, sketch = 0;
    bool flood = false; // not on the wire: a union whose ids count grew past its noise since we sent it (toSend)
};

inline int censusSender(int id) { return id & ((1 << CENSUS_SENDER_BITS) - 1); }

// The sketch bit a dragon id sets in a `bits`-wide hashed set: the same in every process.
inline int censusBit(int id, int bits = CENSUS_SKETCH_BITS) {
    return static_cast<int>(wire::mix(static_cast<std::uint64_t>(id) ^ 0x5bd1e9955bd1e995ULL) % bits);
}

// How many distinct ids a `bits`-wide hashed set holds, by linear counting (Whang et al.): m ln(m / empty bits). A
// full set reads as if half a bit were empty.
inline double sketchCount(std::uint32_t sketch, int bits = CENSUS_SKETCH_BITS) {
    double const m = bits;
    int const empty = bits - std::popcount(sketch);
    return m * std::log(m / std::max(empty, 1) * (empty ? 1 : 2));
}

// The standard error of sketchCount's estimate `n` from a `bits`-wide set (Whang et al.): sqrt(m (e^t - t - 1)) with
// t = n / m.
inline double sketchError(double n, int bits = CENSUS_SKETCH_BITS) {
    double const t = n / bits;
    return std::sqrt(bits * (std::exp(t) - t - 1));
}

// The team's picture of the opponent: counts summed over the `senders` whose records are within P.censusWindow rounds
// (dragon-turns, heads seen, short heads, the longest seen), and the distinct enemy ids in the union of this epoch and
// the last (`ids`), seen by about `observers` of our dragons.
struct CensusEstimate {
    int senders = 0, turns = 0, heads = 0, shorts = 0, longest = 0;
    double ids = 0, observers = 0;

    // Short enemy heads per dragon-turn, -1 with no counts.
    double shortRate() const { return turns ? static_cast<double>(shorts) / turns : -1; }
};

// The team's census as this dragon knows it: every sender's newest counts record (our own among them) and the enemy
// id union of this epoch and the last, each with what we last sent of it.
struct CensusTable {
    struct Entry {
        CensusRecord counts;
        int sent = -1; // round of the last of this sender's counts we sent
    };
    struct Epoch {
        int index = -1;
        std::uint32_t from = 0, sketch = 0;
        std::uint32_t sentFrom = 0, sentSketch = 0; // what of it we last sent ...
        int sent = -1;                               // ... and when
    };
    std::vector<Entry> entries;
    std::array<Epoch, 2> epochs; // by epoch parity

    static bool fresh(CensusRecord const& r, int now) { return now - r.round < P.censusWindow; }

    // Epoch e's slot, emptied first when it held an older epoch.
    Epoch& epoch(int e) {
        Epoch& slot = epochs[e & 1];
        if (slot.index != e)
            slot = Epoch{.index = e};
        return slot;
    }

    // Keeps r if it teaches us something: a counts record newer than its sender's we hold, or ids and observers of
    // this epoch or the last that our union lacks (`now` places its epoch). Returns whether it did.
    bool merge(CensusRecord const& r, int now) {
        if (r.ids) {
            int const e = censusEpochOf(r.epoch, now);
            if (e < 0)
                return false;
            Epoch& slot = epoch(e);
            bool const news = (r.sketch & ~slot.sketch) || (r.from & ~slot.from);
            slot.sketch |= r.sketch;
            slot.from |= r.from;
            return news;
        }
        for (auto& e : entries) {
            if (e.counts.sender != r.sender)
                continue;
            if (e.counts.round >= r.round)
                return false;
            e.counts = r;
            return true;
        }
        entries.push_back({r});
        return true;
    }

    // Forgets senders whose counts have left the window.
    void expire(int now) {
        std::erase_if(entries, [&](Entry const& e) { return !fresh(e.counts, now); });
    }

    void markSent(CensusRecord const& r) {
        if (r.ids) {
            int const e = censusEpochOf(r.epoch, r.round);
            if (Epoch& slot = epochs[e & 1]; e >= 0 && slot.index == e) {
                slot.sentSketch = r.sketch;
                slot.sentFrom = r.from;
                slot.sent = r.round;
            }
            return;
        }
        for (auto& e : entries)
            if (e.counts.sender == r.sender)
                e.sent = r.round;
    }

    // The one census record worth sending this turn and its gossip value, or nothing (Gossip::gather and plan). A
    // counts record goes at once when we never sent its sender's, else again once P.censusResend rounds fresher,
    // and none older than P.gossipMaxAge; an epoch's union at once when we never sent it, else P.censusResend rounds
    // after we last did, for dragons born since and news within its noise. With P.censusFlood a union whose ids count estimate exceeds what we last
    // sent of it by more than that estimate's standard error (every new id while few are known) is `flood` while
    // `mayFlood`: it goes at once and first, worth P.gossipKingValue - 1; without P.censusFlood any news goes at once.
    // Then the highest value (rounds since we last sent it, capped at P.gossipGainCap), a union before counts, the
    // fresher, the lower sender.
    std::optional<std::pair<int, CensusRecord>> toSend(int now, bool mayFlood = true) const {
        std::optional<std::pair<int, CensusRecord>> best;
        auto const consider = [&](CensusRecord const& r, int value) {
            if (r.flood)
                value = P.gossipKingValue - 1;
            if (best && std::make_tuple(value, r.ids, r.round, -r.sender) <=
                            std::make_tuple(best->first, best->second.ids, best->second.round, -best->second.sender))
                return;
            best = std::make_pair(value, r);
        };
        for (auto const& e : entries)
            if ((e.sent < 0 || e.counts.round - e.sent >= P.censusResend) && now - e.counts.round <= P.gossipMaxAge)
                consider(e.counts, std::min(e.counts.round - e.sent, P.gossipGainCap));
        int const e = censusEpoch(now);
        for (Epoch const& slot : epochs) {
            if (slot.index < e - 1 || !slot.sketch)
                continue;
            bool const news = (slot.sketch & ~slot.sentSketch) || (slot.from & ~slot.sentFrom);
            double const was = sketchCount(slot.sentSketch);
            CensusRecord r;
            r.ids = true;
            r.flood = mayFlood && P.censusFlood && sketchCount(slot.sketch) - was > sketchError(was);
            if (!r.flood && slot.sent >= 0 && now - slot.sent < P.censusResend && (P.censusFlood || !news))
                continue;
            r.round = now;
            r.epoch = slot.index & ((1 << CENSUS_EPOCH_BITS) - 1);
            r.from = slot.from;
            r.sketch = slot.sketch;
            consider(r, std::min(now - slot.sent, P.gossipGainCap));
        }
        return best;
    }

    CensusEstimate estimate(int now) const {
        CensusEstimate est;
        for (auto const& e : entries) {
            if (!fresh(e.counts, now))
                continue;
            est.senders++;
            est.turns += e.counts.turns;
            est.heads += e.counts.heads;
            est.shorts += e.counts.shorts;
            est.longest = std::max(est.longest, e.counts.longest);
        }
        std::uint32_t sketch = 0, from = 0;
        for (Epoch const& slot : epochs) {
            if (slot.index >= censusEpoch(now) - 1) {
                sketch |= slot.sketch;
                from |= slot.from;
            }
        }
        est.ids = sketchCount(sketch);
        est.observers = sketchCount(from, CENSUS_FROM_BITS);
        return est;
    }
};

// Our own census, kept across turns by each process (a split child starts empty). Call observe() once a turn after
// World::observe(); it files our newest counts record in the team table and adds the enemy heads we see to the
// epoch's union.
struct Census {
    struct Turn {
        int round, heads, shorts, longest;
    };
    std::deque<Turn> turns;

    void observe(World const& w, CensusTable& table) {
        if (!P.teamCensus)
            return;
        Turn now{w.round, 0, 0, 0};
        CensusTable::Epoch& epoch = table.epoch(censusEpoch(w.round));
        for (auto const& d : w.dragons) {
            if (!d.enemy || d.head < 0)
                continue;
            now.heads++;
            now.shorts += d.visibleLen <= P.censusShortLen;
            now.longest = std::max(now.longest, d.visibleLen);
            epoch.sketch |= 1U << censusBit(d.id);
            epoch.from |= 1U << censusBit(w.myId, CENSUS_FROM_BITS);
        }
        turns.push_back(now);
        while (w.round - turns.front().round >= P.censusWindow)
            turns.pop_front();

        CensusRecord counts;
        counts.round = w.round;
        counts.sender = censusSender(w.myId);
        for (Turn const& t : turns) {
            counts.turns++;
            counts.heads += t.heads;
            counts.shorts += t.shorts;
            counts.longest = std::max(counts.longest, t.longest);
        }
        counts.heads = std::min(counts.heads, CENSUS_COUNT_MAX);
        counts.shorts = std::min(counts.shorts, CENSUS_COUNT_MAX);
        counts.longest = std::min(counts.longest, CENSUS_LONGEST_MAX);
        table.merge(counts, w.round);
        table.expire(w.round);
    }
};

} // namespace bot
