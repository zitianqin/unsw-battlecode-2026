#pragma once

#include "census.hpp"
#include "params.hpp"
#include "sonarkey.hpp"
#include "state.hpp"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <optional>
#include <vector>

namespace bot {

// Side of the square blocks a view record covers; maps are at most 64 x 64, so at most 16 x 16 blocks.
inline constexpr int BLOCK = 4;

// One piece of team knowledge on the wire. A view says which cells of a block the sender (or whoever it
// relays) saw at `round`, which of those held a pearl, and whether an enemy head stood on one. A countdown
// says when one empty tile next tries to spawn, as seen at `round`, and may carry the sender's map mirror.
// A king report says where our king (a
// dragon at least as long as every teammate it knows of, fold.hpp) had its head at `round`, and how long it
// was. A census record carries one dragon's counts of the enemy heads it saw, or the enemy ids the team saw in an
// epoch (census.hpp). Rounds
// travel with relays, so every receiver can keep the fresher of two reports.
struct Record {
    bool countdown = false;
    bool king = false;
    bool census = false;
    int round = 0;
    int where = 0;     // block index for a view, tile for a countdown or a king's head, sender for census counts
    int seenMask = 0;  // view: bit dy * BLOCK + dx set when that cell was observed ...
    int pearlMask = 0; // ... and set here too when it held a pearl
    bool enemy = false; // view: an enemy head stood on an observed cell
    int symmetry = -1; // countdown: the sender's mirror candidate, -1 unknown
    int wait = 0;      // countdown: rounds after `round` until the spawn attempt
    int length = 0;    // king: its length
    int id = 0;        // king: its dragon id
    bool teamB = false; // king: the sender's team, so a copy of this bot on the other side is not followed
    bool heir = false;  // king: the sender is a split-out child that took the crown from its dying parent
    CensusRecord cen;   // census: the record (its round is `round`, a counts record's sender `where`)
    bool claim = false; // this turn's pearl claim's place in gossip's ranking (gossip.hpp); never encoded as a record
};

namespace wire {
inline constexpr int BODY_BITS = 51;
inline constexpr std::uint64_t BODY_MASK = (1ULL << BODY_BITS) - 1;

// What every dragon of our team knows and the wire doesn't carry, covered by the tag: our side, and the map's
// size. A record decodes only under the context it was encoded for.
struct Context {
    bool teamB = false;
    int W = 0, H = 0;
};

// The context as one hash word: our side (with P.sonarTeamTag) and the map size.
inline std::uint64_t contextWord(Context const& c) {
    std::uint64_t const side = P.sonarTeamTag ? (c.teamB ? 2ULL : 1ULL) : 0;
    return side | static_cast<std::uint64_t>(c.W) << 8 | static_cast<std::uint64_t>(c.H) << 24;
}

// The plain value's top 13 bits (with P.sonarRoundMask, wire::seal replaces them on the air): a keyed check that
// drops enemies' messages and garbled values. With P.sonarTeamTag it also covers the sender's team side (not
// sent), so a copy of this bot on the other team fails the check. With P.sonarSipTag it is SipHash-2-4 under our
// build's key (sonarkey.hpp) over the body and the whole context, so values heard in another build's games or on
// a map of another size fail too.
inline std::uint64_t tagOf(std::uint64_t body, Context const& c) {
    if (P.sonarSipTag)
        return sipHash(KEY0, KEY1, body, contextWord(c)) >> BODY_BITS;
    std::uint64_t const side = P.sonarTeamTag ? (c.teamB ? 2ULL : 1ULL) : 0;
    return mix((body | side << BODY_BITS) ^ KEY0) >> BODY_BITS;
}

// With P.sonarRoundMask what goes on the air is sealed per round: the body is XORed with a keyed SipHash of the
// send round and the context, so a replay reader sees a fresh random body every round, and the top 13 bits carry
// a SipHash check of the sealed body under that round. A message is read on the recipient's next turn, in the send
// round or the next (docs/unsw/sonar.md), so open() tries the heard round, then the one before, and the check picks
// the send round. It turns a heard value back into the plain value (body and tag) the decoders read, and openAll
// drops the values that fail both; relays are re-encoded, so re-sealed, at send.
// Both sides share the build key, so the mask and the check also cover the context (our side).
inline std::uint64_t roundMask(int round, Context const& c) {
    return sipHash(KEY0, KEY1, static_cast<std::uint64_t>(round), contextWord(c) | 1ULL << 63) & BODY_MASK;
}

inline std::uint64_t sealCheck(std::uint64_t sealed, int round, Context const& c) {
    return sipHash(KEY0, KEY1, sealed, contextWord(c) | static_cast<std::uint64_t>(round) << 40 | 1ULL << 62) >>
           BODY_BITS;
}

// The value to send at `round` for the plain value `plain` (encodeRecord's and the other encoders' output).
inline std::uint64_t seal(std::uint64_t plain, int round, Context const& c) {
    if (!P.sonarRoundMask)
        return plain;
    std::uint64_t const sealed = (plain & BODY_MASK) ^ roundMask(round, c);
    return sealed | sealCheck(sealed, round, c) << BODY_BITS;
}

// The plain value of `value` heard at `round`, or nothing when its check fails for this round and the one before.
inline std::optional<std::uint64_t> open(std::uint64_t value, int round, Context const& c) {
    if (!P.sonarRoundMask)
        return value;
    std::uint64_t const sealed = value & BODY_MASK;
    for (int sent : {round, round - 1}) {
        if (sent < 0 || value >> BODY_BITS != sealCheck(sealed, sent, c))
            continue;
        std::uint64_t const body = sealed ^ roundMask(sent, c);
        return body | tagOf(body, c) << BODY_BITS;
    }
    return std::nullopt;
}

// The heard values that open (open), in order, as plain values.
inline std::vector<std::uint64_t> openAll(std::vector<std::uint64_t> const& values, int round, Context const& c) {
    std::vector<std::uint64_t> plain;
    for (std::uint64_t const v : values)
        if (auto const p = open(v, round, c))
            plain.push_back(*p);
    return plain;
}

inline std::uint64_t field(int value, int bits) {
    return static_cast<std::uint64_t>(std::clamp(value, 0, (1 << bits) - 1));
}
} // namespace wire

// Packs a record into a 51-bit body under the tag. Bit 0 is the kind. A view is round 9 bits, block 8,
// seen mask 16, pearl mask 16, enemy 1; a countdown is round 9, tile 12, wait 9, then bit 31 clear and
// mirror + 1 in bits 32-33 (0 unknown). kelp.hpp adds an optional edge in bits 34-46 and presence in 47.
// A king report is a countdown with bit 31 set (older versions drop it):
// round 9, head tile 12, length 9, then id 16, team 1 and
// heir 1 (versions before the heir bit drop a report with it set). A census record is a king report with bit 50 set
// (older versions drop it): bits 1-30 and 32-49 hold round 9, then for counts sender 12, a clear bit, turns 6,
// heads 7, shorts 7 and longest 6, for a union epoch 5, observers 7, a set bit and the 26-bit ids sketch. `ctx` (our
// side, the map) is covered by the tag. A countdown-kind body with bit 48 set and bit 31 clear is a doorway probe
// (doorway.hpp), which this decoder drops, and one with bit 49 set and bits 48 and 50 clear is a listen record
// (listen.hpp's encodeDetection), dropped here too.
inline std::uint64_t encodeRecord(Record const& r, wire::Context const& ctx) {
    std::uint64_t body = 0;
    if (r.census) {
        CensusRecord const& c = r.cen;
        std::uint64_t const who = c.ids ? wire::field(c.epoch, CENSUS_EPOCH_BITS) |
                                              (c.from & ((1ULL << CENSUS_FROM_BITS) - 1)) << CENSUS_EPOCH_BITS
                                        : wire::field(c.sender, CENSUS_SENDER_BITS);
        std::uint64_t const data = c.ids ? c.sketch & ((1ULL << CENSUS_SKETCH_BITS) - 1)
                                         : wire::field(c.turns, 6) | wire::field(c.heads, 7) << 6 |
                                               wire::field(c.shorts, 7) << 13 | wire::field(c.longest, 6) << 20;
        std::uint64_t const payload =
            wire::field(c.round, 9) | who << 9 | static_cast<std::uint64_t>(c.ids) << 21 | data << 22;
        body = 1 | (payload & ((1ULL << 30) - 1)) << 1 | 1ULL << 31 | (payload >> 30) << 32 | 1ULL << 50;
    } else if (r.king) {
        body = 1 | wire::field(r.round, 9) << 1 | wire::field(r.where, 12) << 10 | wire::field(r.length, 9) << 22 |
               1ULL << 31 | wire::field(r.id, 16) << 32 | static_cast<std::uint64_t>(r.teamB) << 48 |
               static_cast<std::uint64_t>(r.heir) << 49;
    } else if (r.countdown) {
        body = 1 | wire::field(r.round, 9) << 1 | wire::field(r.where, 12) << 10 | wire::field(r.wait, 9) << 22 |
               wire::field(r.symmetry + 1, 2) << 32;
    } else {
        body = wire::field(r.round, 9) << 1 | wire::field(r.where, 8) << 10 | wire::field(r.seenMask, 16) << 18 |
               wire::field(r.pearlMask, 16) << 34 | static_cast<std::uint64_t>(r.enemy) << 50;
    }
    return body | wire::tagOf(body, ctx) << wire::BODY_BITS;
}

// The record in `value`, or nothing when its tag does not match our context `ctx` or its fields contradict
// each other.
inline std::optional<Record> decodeRecord(std::uint64_t value, wire::Context const& ctx) {
    std::uint64_t const body = value & wire::BODY_MASK;
    if (value >> wire::BODY_BITS != wire::tagOf(body, ctx))
        return std::nullopt;
    auto const bits = [&](int shift, int width) { return static_cast<int>((body >> shift) & ((1ULL << width) - 1)); };
    Record r;
    r.countdown = body & 1;
    r.round = bits(1, 9);
    if (r.countdown && (body >> 31 & 1) && (body >> 50)) {
        std::uint64_t const payload = (body >> 1 & ((1ULL << 30) - 1)) | (body >> 32 & ((1ULL << 18) - 1)) << 30;
        auto const at = [&](int shift, int width) { return static_cast<int>(payload >> shift & ((1ULL << width) - 1)); };
        r.countdown = false;
        r.census = true;
        CensusRecord& c = r.cen;
        c.round = r.round = at(0, 9);
        c.ids = at(21, 1);
        if (c.ids) {
            c.epoch = at(9, CENSUS_EPOCH_BITS);
            c.from = static_cast<std::uint32_t>(at(9 + CENSUS_EPOCH_BITS, CENSUS_FROM_BITS));
            c.sketch = static_cast<std::uint32_t>(at(22, CENSUS_SKETCH_BITS));
            if (!c.sketch || !c.from || censusEpochOf(c.epoch, c.round) < 0)
                return std::nullopt;
        } else {
            c.sender = r.where = at(9, CENSUS_SENDER_BITS);
            c.turns = at(22, 6);
            c.heads = at(28, 7);
            c.shorts = at(35, 7);
            c.longest = at(42, 6);
            if (c.turns == 0 || c.shorts > c.heads || (c.heads == 0) != (c.longest == 0))
                return std::nullopt;
        }
    } else if (r.countdown && (body >> 31 & 1)) {
        r.countdown = false;
        r.king = true;
        r.where = bits(10, 12);
        r.length = bits(22, 9);
        r.id = bits(32, 16);
        r.teamB = bits(48, 1);
        r.heir = bits(49, 1);
    } else if (r.countdown) {
        if (body >> 34 && (!(body >> 47 & 1) || body >> 48))
            return std::nullopt;
        r.where = bits(10, 12);
        r.wait = bits(22, 9);
        r.symmetry = bits(32, 2) - 1;
    } else {
        r.where = bits(10, 8);
        r.seenMask = bits(18, 16);
        r.pearlMask = bits(34, 16);
        r.enemy = bits(50, 1);
        if (r.seenMask == 0 || (r.pearlMask & ~r.seenMask))
            return std::nullopt;
    }
    return r;
}

// The team's picture of the whole map, kept by every dragon and merged over sonar (gossip.hpp): for each
// tile the freshest round anyone saw it, whether it held a pearl then, and its freshest known countdown; for
// each block, the freshest enemy sighting; the team's census of the opponent (census.hpp). Our own vision is just
// another (always fresh) report. Merging
// keeps the fresher stamp per cell, so an old or relayed report never overwrites a newer one.
// Call observe() after World::observe(), then hear() with the turn's messages.
struct Belief {
    int W = 0, H = 0, N = 0, BW = 0, BH = 0;
    std::vector<int> seen;          // tile -> freshest round anyone saw it, -1 never
    std::vector<int> pearl;         // tile -> round a pearl was last reported on it; present iff == seen
    std::vector<int> spawn;         // tile -> round of its next spawn attempt from the freshest countdown, -1 unknown
    std::vector<int> spawnSeen;     // tile -> round that countdown was read, -1 none
    std::vector<int> blockFresh;    // block -> freshest seen round among its cells
    std::vector<int> blockSent;     // block -> round of the last view of it we sent
    std::vector<int> spawnSent;     // tile -> round of the last countdown of it we sent
    std::vector<int> enemyRound;    // block -> round of the freshest report on enemy heads in it, -1 none
    std::vector<std::uint8_t> enemyThere;
    std::vector<int> likelyRound;    // block -> round of the freshest sender set touching it (noteSenders), -1 none
    std::vector<double> likelyShare; // block -> the chance that set put a sender in it
    std::vector<int> recentSpawns;  // every tile whose countdown was read within P.gossipMaxAge rounds (and a few
    std::vector<std::uint8_t> spawnListed; // older ones), so gossip need not scan the map; tile -> listed there
    int sharedSymmetry = -1; // a teammate's mirror, used only while World::symLive still permits it
    bool teamB = false;
    struct KingReport {
        Record rec;
        int sent = -1; // round of the last report of this king we sent
        int heirRound = -1; // the earliest heir report we heard of it (close to its birth): its round ...
        int heirLength = 0; // ... and length
    };
    std::vector<KingReport> kings; // the freshest report of each king we heard (or are), by id
    int heirOf = -1;    // the king we were split out of (fold.hpp's findParentKing): we ignore its reports ...
    int heirOfRound = -1; // ... filed up to this round (our birth), after which it can only be a live king
    CensusTable census;   // the team's census of the opponent (census.hpp)

    void init(World const& w) {
        teamB = w.teamB;
        sharedSymmetry = -1;
        kings.clear();
        census = {};
        heirOf = -1;
        heirOfRound = -1;
        W = w.W;
        H = w.H;
        N = w.N;
        BW = (W + BLOCK - 1) / BLOCK;
        BH = (H + BLOCK - 1) / BLOCK;
        seen.assign(N, -1);
        pearl.assign(N, -1);
        spawn.assign(N, -1);
        spawnSeen.assign(N, -1);
        spawnSent.assign(N, -1);
        blockFresh.assign(BW * BH, -1);
        blockSent.assign(BW * BH, -1);
        enemyRound.assign(BW * BH, -1);
        enemyThere.assign(BW * BH, 0);
        likelyRound.assign(BW * BH, -1);
        likelyShare.assign(BW * BH, 0);
        recentSpawns.clear();
        spawnListed.assign(N, 0);
    }

    // Sets tile t's freshest countdown: its next spawn attempt at `when`, read at round `r`.
    void setSpawn(int t, int when, int r) {
        spawn[t] = when;
        spawnSeen[t] = r;
        if (!spawnListed[t]) {
            spawnListed[t] = 1;
            recentSpawns.push_back(t);
        }
    }

    int blocks() const { return BW * BH; }
    int blockOf(int t) const { return (t / W) / BLOCK * BW + (t % W) / BLOCK; }

    // The tile at cell i (dy * BLOCK + dx) of block b, -1 past the map's edge.
    int cellTile(int b, int i) const {
        int const x = b % BW * BLOCK + i % BLOCK, y = b / BW * BLOCK + i / BLOCK;
        return x < W && y < H ? y * W + x : -1;
    }

    bool pearlAt(int t) const { return seen[t] >= 0 && pearl[t] == seen[t]; }

    void see(int t, int r, bool hasPearl) {
        seen[t] = r;
        if (hasPearl)
            pearl[t] = r;
        int const b = blockOf(t);
        blockFresh[b] = std::max(blockFresh[b], r);
    }

    // Records this turn's vision: every visible tile, its countdown, and which blocks show an enemy head.
    void observe(World const& w) {
        std::erase_if(recentSpawns, [&](int t) {
            bool const old = w.round - spawnSeen[t] > P.gossipMaxAge;
            if (old)
                spawnListed[t] = 0;
            return old;
        });
        for (int t : w.touched) {
            if (!w.visible(t))
                continue;
            see(t, w.round, w.pearlNow[t] != 0);
            if (w.spawnKind[t] == SPAWN_YES)
                setSpawn(t, w.spawnRound[t], w.round);
            int const b = blockOf(t);
            if (enemyRound[b] != w.round) {
                enemyRound[b] = w.round;
                enemyThere[b] = 0;
            }
            if (w.occId[t] >= 0 && w.occHead[t] && w.occEnemy[t])
                enemyThere[b] = 1;
        }
    }

    // Our move lands on `t`: any pearl there is ours, so we stop reporting it.
    void noteEaten(int t) {
        if (t >= 0)
            pearl[t] = -1;
    }

    // Keeps king report r if it is the freshest of that king; reports from the other team are dropped. The
    // earliest heir report of each king is kept too (superseded).
    bool mergeKing(Record const& r) {
        if (r.teamB != teamB)
            return false;
        for (auto& k : kings) {
            if (k.rec.id != r.id)
                continue;
            if (r.heir && (k.heirRound < 0 || r.round < k.heirRound)) {
                k.heirRound = r.round;
                k.heirLength = r.length;
            }
            if (r.round <= k.rec.round)
                return false;
            k.rec = r;
            return true;
        }
        kings.push_back({r});
        if (r.heir) {
            kings.back().heirRound = r.round;
            kings.back().heirLength = r.length;
        }
        return true;
    }

    void markKingSent(Record const& r) {
        for (auto& k : kings)
            if (k.rec.id == r.id)
                k.sent = std::max(k.sent, r.round);
    }

    // With P.kingSuccession, whether king report k is of a dead king whose split-out child has taken the crown:
    // a king whose current report is a fresh heir's (fold.hpp's reportKing), of a later id, and whose earliest heir
    // report we heard (close to its birth: the parent's last report is filed no later than the child's birth, and
    // any report after it is a live king's) was filed no earlier than k, at most MIN_SIZE shorter than k (a child is
    // its parent's length less the MIN_SIZE the parent keeps), with its head now no further from k's head than a
    // child's head (the parent's old tail, within the parent's length less one of its head) could be after both
    // moved one tile a round since k.
    bool superseded(Record const& k, int now, int maxAge) const {
        if (!P.kingSuccession)
            return false;
        for (auto const& h : kings) {
            Record const& r = h.rec;
            if (!r.heir || h.heirRound < 0 || r.id <= k.id || now - r.round > maxAge || k.round > h.heirRound ||
                k.length > h.heirLength + unswbc::Constants::MIN_SIZE)
                continue;
            int dx = std::abs(k.where % W - r.where % W), dy = std::abs(k.where / W - r.where / W);
            dx = std::min(dx, W - dx);
            dy = std::min(dy, H - dy);
            if (dx + dy <= h.heirLength + unswbc::Constants::MIN_SIZE - 1 + (r.round - k.round))
                return true;
        }
        return false;
    }

    // The longest king reported within `maxAge` rounds of `now` other than `exceptId` (the lower id on a tie, as
    // mergesInto decides, so every relay forwards the same one), or nullptr. Our dead parent king's reports
    // (heirOf) and kings superseded by an heir are skipped.
    KingReport const* bestKing(int now, int maxAge, int exceptId) const {
        KingReport const* best = nullptr;
        for (auto const& k : kings) {
            if (k.rec.id == exceptId || (k.rec.id == heirOf && k.rec.round <= heirOfRound) ||
                now - k.rec.round > maxAge)
                continue;
            if (best && (k.rec.length < best->rec.length ||
                         (k.rec.length == best->rec.length && k.rec.id > best->rec.id)))
                continue;
            if (!superseded(k.rec, now, maxAge))
                best = &k;
        }
        return best;
    }

    // As bestKing, but the best report that `accept` takes (asked best first, only until one passes), or nullptr:
    // fold.hpp's foldKing gives each report older than P.kingReportAge its own age window, which needs a path fill.
    template <class Accept>
    KingReport const* bestKingIf(int now, int maxAge, int exceptId, Accept&& accept) const {
        std::vector<KingReport const*> ranked;
        for (auto const& k : kings)
            if (k.rec.id != exceptId && !(k.rec.id == heirOf && k.rec.round <= heirOfRound) &&
                now - k.rec.round <= maxAge && !superseded(k.rec, now, maxAge))
                ranked.push_back(&k);
        std::sort(ranked.begin(), ranked.end(), [](KingReport const* a, KingReport const* c) {
            return a->rec.length != c->rec.length ? a->rec.length > c->rec.length : a->rec.id < c->rec.id;
        });
        for (auto const* k : ranked)
            if (accept(*k))
                return k;
        return nullptr;
    }

    // The longest length of any king report we hold other than `exceptId`'s, whatever its age (0 when none): a king
    // report reaches few dragons and ages out in P.kingReportAge rounds, so this remembers that a longer teammate
    // existed after its report stopped reaching us. As in bestKing, our dead parent king's reports (heirOf) and kings
    // superseded by an heir (any heir report we hold, whatever its age) are skipped.
    int longestHeard(int now, int exceptId) const {
        int longest = 0;
        for (auto const& k : kings)
            if (k.rec.id != exceptId && !(k.rec.id == heirOf && k.rec.round <= heirOfRound) &&
                !superseded(k.rec, now, now))
                longest = std::max(longest, k.rec.length);
        return longest;
    }

    // Merges one report heard at round `now`. Returns whether it taught us anything fresher.
    bool merge(Record const& r, int now) {
        if (r.round > now)
            return false;
        if (r.king)
            return mergeKing(r);
        if (r.census)
            return P.teamCensus && census.merge(r.cen, now);
        if (r.countdown) {
            int const t = r.where;
            if (t >= N || r.round <= spawnSeen[t])
                return false;
            if (r.round > seen[t])
                see(t, r.round, false);
            setSpawn(t, r.round + r.wait, r.round);
            return true;
        }
        int const b = r.where;
        if (b >= blocks())
            return false;
        for (int i = 0; i < BLOCK * BLOCK; i++)
            if ((r.seenMask >> i & 1) && cellTile(b, i) < 0)
                return false;
        bool fresher = false;
        for (int i = 0; i < BLOCK * BLOCK; i++) {
            int const t = cellTile(b, i);
            if ((r.seenMask >> i & 1) && seen[t] < r.round) {
                see(t, r.round, r.pearlMask >> i & 1);
                fresher = true;
            }
        }
        if (r.round > enemyRound[b]) {
            enemyRound[b] = r.round;
            enemyThere[b] = r.enemy;
        } else if (r.round == enemyRound[b] && r.enemy) {
            enemyThere[b] = 1;
        }
        return fresher;
    }

    wire::Context context() const { return {teamB, W, H}; }

    // Merges this turn's sonar messages; values that are not our records are ignored. With P.sonarDropStale so
    // are records older than any teammate sends (P.gossipMaxAge or P.kingReportAge, plus the round a message can
    // wait for our turn): replays of our old values. Records from a later round were never merged. An exact
    // repeat of a value we already heard is a no-op in merge, since every stamp only moves forward. A valid
    // countdown can also teach its mirror, even when we already know that countdown; local contradictions win.
    void hear(World const& w, std::vector<std::uint64_t> const& messages) {
        int const maxAge = std::max(P.gossipMaxAge, P.kingReportAge) + 1;
        for (std::uint64_t const message : messages)
            if (auto const r = decodeRecord(message, context()); r && r->round <= w.round &&
                !(P.sonarDropStale && w.round - r->round > maxAge)) {
                if (P.shareSymmetry && r->countdown && r->where < N && r->symmetry >= 0 &&
                    (w.symLive >> r->symmetry & 1) &&
                    (sharedSymmetry < 0 || !(w.symLive >> sharedSymmetry & 1)))
                    sharedSymmetry = r->symmetry;
                merge(*r, w.round);
            }
    }

    // The freshest slice of block b we know: the cells last seen at round r, with their pearls.
    Record viewOf(int b, int r) const {
        Record rec;
        rec.round = r;
        rec.where = b;
        int const x0 = b % BW * BLOCK, y0 = b / BW * BLOCK;
        int const xs = std::min(BLOCK, W - x0), ys = std::min(BLOCK, H - y0);
        for (int dy = 0; dy < ys; dy++) {
            for (int dx = 0, t = (y0 + dy) * W + x0; dx < xs; dx++, t++) {
                if (seen[t] != r)
                    continue;
                int const i = dy * BLOCK + dx;
                rec.seenMask |= 1 << i;
                if (pearl[t] == r)
                    rec.pearlMask |= 1 << i;
            }
        }
        rec.enemy = enemyRound[b] == r && enemyThere[b];
        return rec;
    }

    // An enemy head on tile t at round `r` that no view of ours holds (a decoded enemy report, listen.hpp, or a
    // sender set narrowed to one block, noteSenders), filed as the one-cell view a teammate would send: t seen at r
    // with no pearl (a head stands there) and an enemy head in its block. So gossip relays it like any sighting. A tile
    // anyone saw at r or later keeps that view (a report that disagrees with what we saw is not believed), and so does a
    // block with a fresher enemy report than r. Returns whether it was filed.
    bool noteEnemyHead(int t, int r, int now) {
        if (t < 0 || t >= N || r > now || seen[t] >= r || enemyRound[blockOf(t)] > r)
            return false;
        int const b = blockOf(t);
        int const x = t % W - b % BW * BLOCK, y = t / W - b / BW * BLOCK;
        Record rec;
        rec.round = r;
        rec.where = b;
        rec.seenMask = 1 << (y * BLOCK + x);
        rec.enemy = true;
        return merge(rec, now);
    }

    // With P.inferToBelief, where the senders of this turn's unreadable values can be (infer.hpp's tiles, at round
    // `now`): each block's share of the set times P.inferHit (the share of heard values whose sender was in it) is a
    // weaker enemy mark, read by enemyChance. A block holding enough of the set that a sender is more likely there
    // than not is filed as a sighting (noteEnemyHead on its set tile with no pearl known that we saw longest ago), so
    // gossip relays it: a yes/no view bit is wrong least often when it rounds at even odds.
    void noteSenders(std::vector<int> const& tiles, int now) {
        if (!P.inferToBelief || tiles.empty())
            return;
        if (likelyRound.size() != static_cast<std::size_t>(blocks())) {
            likelyRound.assign(blocks(), -1);
            likelyShare.assign(blocks(), 0);
        }
        std::vector<int> count(blocks(), 0);
        for (int t : tiles)
            count[blockOf(t)]++;
        double const n = static_cast<double>(tiles.size());
        for (int b = 0; b < blocks(); b++) {
            if (!count[b])
                continue;
            double const share = P.inferHit * count[b] / n;
            likelyShare[b] = likelyRound[b] == now ? std::max(likelyShare[b], share) : share;
            likelyRound[b] = now;
            if (share <= 0.5)
                continue;
            int pick = -1;
            for (int t : tiles)
                if (blockOf(t) == b && !pearlAt(t) && (pick < 0 || seen[t] < seen[pick]))
                    pick = t;
            if (pick >= 0)
                noteEnemyHead(pick, now, now);
        }
    }

    // Chance an enemy head is in block b now: the last report, fading to P.enemyPrior with half-life
    // P.enemyHalfLife rounds; P.enemyPrior for a block nobody has reported on. With P.inferToBelief, a sender set
    // fresher than the last report (noteSenders) weighs in at its share, fading the same way.
    double enemyChance(int b, int now) const {
        auto const faded = [&](double observed, int r) {
            return P.enemyPrior + (observed - P.enemyPrior) * std::exp2(-static_cast<double>(now - r) / P.enemyHalfLife);
        };
        double chance = enemyRound[b] < 0 ? P.enemyPrior : faded(enemyThere[b] ? 1.0 : 0.0, enemyRound[b]);
        if (P.inferToBelief && !likelyRound.empty() && likelyRound[b] > enemyRound[b])
            chance = std::max(chance, faded(likelyShare[b], likelyRound[b]));
        return chance;
    }
};

} // namespace bot
