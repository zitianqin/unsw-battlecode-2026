#pragma once

#include "belief.hpp"
#include "claims.hpp"
#include "doorway.hpp"
#include "fold.hpp"
#include "helper.hpp"
#include "kelp.hpp"
#include "listen.hpp"
#include "state.hpp"

#include <algorithm>
#include <array>
#include <bit>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <vector>

namespace bot {

// The direction from our head into our neck, -1 when unknown.
inline int neckDir(World const& w) {
    for (int dir = 0; w.body.size() >= 2 && dir < 4; dir++)
        if (w.step(w.head, dir) == w.body[1])
            return dir;
    return -1;
}

// Spreads the belief map (belief.hpp) over protocol-3 sonar. After the action is chosen, plan() picks the
// reports that are fresher than what we last sent of the same block, tile or census sender (our own vision and
// anything we heard), most valuable first, and the directions whose ray can reach a teammate; emit() writes them
// before the turn's one flush. Relaying what we heard lets news cross the swarm beyond any one line of sight.
struct Gossip {
    std::array<std::uint64_t, 4> out{};
    std::array<bool, 4> sending{};
    KelpKnowledge kelp;
    // This turn's pearl claim (claims.hpp, P.pearlClaims), set by main before plan(), and the last one we sent.
    std::optional<PearlClaim> claim;
    PearlClaim claimSent{.eat = -1, .tile = -1};
    int claimSentRound = -1;
    // The team's detection of the enemy's team (listen.hpp, P.listenShare), set by main each turn, and the last one we
    // sent: one naming another team, or one unsent for P.gossipMaxAge rounds (as old as any report we still
    // send, so a dragon out of range then or born since hears it no later than our other news), takes the first ray that would carry
    // gossip (a rising confidence alone waits for the resend).
    listen::Detection detection, detectionSent;
    int detectionSentRound = -1;

    // Merge wall messages after local vision and symmetry. Directly seen open/portal edges always win;
    // received walls update only unknown edges through World::setEdge, including its cached paths.
    void hearWalls(World& w, Belief const& b, std::vector<std::uint64_t> const& messages) {
        if (!P.shareKelpEdges)
            return;
        if (kelp.seen.empty())
            kelp.init(w);
        kelp.observe(w);
        kelp.hear(w, messages, b.context());
        kelp.apply(w);
    }

    // Whether a ray cast from `origin` towards `dir` can reach a teammate, as far as we can see: not when it
    // stops on kelp, our own body or an enemy first. `backDir` points into our own neck: that ray leaves
    // through our tail along its outward facing, which reaches the split child when the cut is straight.
    static bool rayUseful(World const& w, int origin, int dir, int backDir) {
        if (dir == backDir)
            return true;
        int cur = origin;
        for (int steps = 0; steps < w.W + w.H; steps++) {
            int const next = w.step(cur, dir);
            if (next == KELP)
                return false;
            if (next == UNKNOWN_PORTAL)
                return true;
            if (w.occId[next] >= 0)
                return w.occId[next] != w.myId && !w.occEnemy[next];
            if (!w.visible(next))
                return true;
            cur = next;
        }
        return false;
    }

    // Reports worth sending this turn, best first. A report's value is how much fresher it is than the last
    // one we sent of the same block or tile (capped), plus a bonus per pearl it announces (a countdown about
    // to fire counts as one). Reports older than P.gossipMaxAge rounds are not worth the airtime. With
    // P.kingBeacon, the longest king we know of (fold.hpp) goes first whenever we hold a fresher report of it. With
    // P.teamCensus and not P.censusSpareOnly, the turn's census record (CensusTable::toSend) competes too, ahead of a
    // view or countdown of equal value (a union to flood outranks them all but a king report).
    // Only the best `limit` are returned (plan() sends at most one per direction); no two reports rank equal, so
    // they come out as the head of the full list.
    // With `claim`, our pearl claim competes for a ray like a countdown: rounds since we last sent this same claim
    // (`claimSent`, at `claimSentRound`; never sent counts as the cap) plus one pearl.
    static std::vector<Record> gather(World const& w, Belief const& b, std::size_t limit = SIZE_MAX,
                                      std::optional<PearlClaim> const& claim = std::nullopt,
                                      PearlClaim const& claimSent = {}, int claimSentRound = -1) {
        struct Scored {
            int value;
            Record rec;
        };
        std::vector<Scored> scored;
        for (int block = 0; block < b.blocks(); block++) {
            int const r = b.blockFresh[block];
            if (r < 0 || r <= b.blockSent[block] || w.round - r > P.gossipMaxAge)
                continue;
            Record const rec = b.viewOf(block, r);
            int const gain = std::min(r - b.blockSent[block], P.gossipGainCap);
            scored.push_back({gain + P.gossipPearlValue * std::popcount(static_cast<unsigned>(rec.pearlMask)), rec});
        }
        for (int t : b.recentSpawns) {
            int const r = b.spawnSeen[t];
            if (r < 0 || r <= b.spawnSent[t] || w.round - r > P.gossipMaxAge || b.pearlAt(t))
                continue;
            int const wait = b.spawn[t] - w.round;
            if (wait < 1 || wait > P.gossipCountdownMax)
                continue;
            Record rec;
            rec.countdown = true;
            rec.round = r;
            rec.where = t;
            rec.wait = b.spawn[t] - r;
            scored.push_back({std::min(r - b.spawnSent[t], P.gossipGainCap) + P.gossipPearlValue, rec});
        }
        if (P.teamCensus && !P.censusSpareOnly)
            if (auto const c = censusRecord(w, b))
                scored.push_back({c->first, c->second});
        if (P.kingBeacon) {
            auto const* k = b.bestKing(w.round, P.kingReportAge, -1);
            if (k && k->rec.round > k->sent)
                scored.push_back({P.gossipKingValue, k->rec});
        }
        if (claim) {
            bool const same = claim->tile == claimSent.tile && claim->eat == claimSent.eat;
            Record rec;
            rec.claim = true;
            rec.where = claim->tile;
            scored.push_back(
                {(same ? std::min(w.round - claimSentRound, P.gossipGainCap) : P.gossipGainCap) + P.gossipPearlValue,
                 rec});
        }
        auto const kept = scored.begin() + static_cast<std::ptrdiff_t>(std::min(limit, scored.size()));
        std::partial_sort(scored.begin(), kept, scored.end(), [](Scored const& a, Scored const& c) {
            if (a.value != c.value)
                return a.value > c.value;
            if (a.rec.census != c.rec.census)
                return a.rec.census;
            if (a.rec.countdown != c.rec.countdown)
                return !a.rec.countdown;
            return a.rec.where < c.rec.where;
        });
        scored.erase(kept, scored.end());
        std::vector<Record> records;
        for (auto const& s : scored)
            records.push_back(s.rec);
        return records;
    }

    // The turn's census record (CensusTable::toSend) as a record to send, with its gossip value; from the fold's start
    // no union floods, since flooding cut the king reports our dragons heard there.
    static std::optional<std::pair<int, Record>> censusRecord(World const& w, Belief const& b) {
        auto const c = b.census.toSend(w.round, w.round < foldStart());
        if (!c)
            return std::nullopt;
        Record rec;
        rec.census = true;
        rec.cen = c->second;
        rec.round = rec.cen.round;
        rec.where = rec.cen.sender;
        return std::make_pair(c->first, rec);
    }

    // A truthful countdown to send toward a split child's first inbox. Prefer the freshest
    // non-pearl tile, even if its report was already sent. If none exists (a barren view), keep normal gossip.
    static std::optional<Record> childSymmetry(World const& w, Belief const& b) {
        if (!P.shareSymmetry || !P.useSymmetry || w.symmetry < 0)
            return std::nullopt;
        std::optional<Record> best;
        for (int t : b.recentSpawns) {
            int const r = b.spawnSeen[t], wait = b.spawn[t] - r;
            if (r < 0 || r > w.round || w.round - r > P.gossipMaxAge || b.pearlAt(t) || wait < 0 ||
                wire::field(wait, 9) != static_cast<std::uint64_t>(wait))
                continue;
            if (best && (r < best->round || (r == best->round && t >= best->where)))
                continue;
            Record rec;
            rec.countdown = true;
            rec.round = r;
            rec.where = t;
            rec.wait = wait;
            rec.symmetry = w.symmetry;
            best = rec;
        }
        return best;
    }

    // Chooses this turn's messages. `origin` is our head after the action; `backDir` is the direction from it
    // into our neck. Record i goes out on the i-th useful direction; with P.censusSpareOnly spare directions carry
    // the turn's census record (censusRecord: the first spare one, or every spare one for a union to flood), and then
    // spare directions repeat the list. Our pearl claim (`claim`) is one of the list's records, ranked by gather, and
    // marked sent in claimSent. Sent
    // reports are marked so we only send them again once something fresher arrives (a census record: P.censusResend
    // rounds fresher). A king report leads the list, and the directions are then tried from one that turns with the
    // round, so the beacon spreads every way rather than always north; from beaconStart the king's report of
    // itself goes out on every useful direction (a king seeding earlier, fold.hpp's kingSeedOn, leaves the rest to
    // pearl gossip), and so does a census union to flood that leads the list (not P.censusSpareOnly). On a split turn, a countdown carrying our mirror takes the neck ray when one is available; a
    // bend at the cut can make that ray miss the child. With P.shareKelpEdges, a local wall block takes another ray
    // known to reach the child (the neck ray too when no mirror countdown needs it) when it carries walls outside the
    // child's first view; countdowns keep their contents and add one wall edge in spare bits. `childLength` is
    // the actual split size. With P.doorwayProbe a doorway probe (doorway.hpp) takes each portal edge's direction
    // first, gossip or not. An `origin` below 0 (a step through a portal whose far end we don't know) sends no probe,
    // and gossip plans from the head we had. With a `detection` due (P.listenShare) the first ray that would carry
    // gossip, other than a split's neck ray, carries the listen record (listen.hpp's encodeDetection) instead; on a
    // split turn the record also takes a ray known to reach the child, due or not, other than the mirror countdown's
    // (the wall block then takes a third such ray, or none).
    void plan(World const& w, Belief& b, int origin, int backDir, bool split = false, int childLength = 0) {
        sending.fill(false);
        int const tailDir = backDir >= 0 ? backDir : (w.facing[w.head] + 2) % 4;
        auto const probes = doorProbes(w, b.context(), origin, tailDir);
        if (origin < 0)
            origin = w.head;
        for (int dir = 0; dir < 4; dir++)
            if (probes[dir]) {
                sending[dir] = true;
                out[dir] = *probes[dir];
            }
        if (!P.teamGossip)
            return;
        std::vector<Record> const records = gather(w, b, 4, claim, claimSent, claimSentRound);
        auto const child = split && backDir >= 0 ? childSymmetry(w, b) : std::nullopt;
        auto const census = P.teamCensus && P.censusSpareOnly ? censusRecord(w, b) : std::nullopt;
        if (P.shareKelpEdges && !kelp.seen.empty())
            kelp.localEdges(w, split && static_cast<int>(w.body.size()) == w.length ? w.body.back() : -1);
        // A split child is a new process that knows nothing: the team's detection goes to it on a ray known to reach
        // it (KelpKnowledge::childRay), so it inherits the enemy's team at once.
        int const inherit = split && detection.format >= 0
            ? KelpKnowledge::childRay(w, childLength, child ? backDir : -1) : -1;
        auto const wall = P.shareKelpEdges && split && !kelp.seen.empty()
            ? kelp.childPacket(w, childLength, b.context(), child ? backDir : -1, inherit) : std::nullopt;
        if (records.empty() && !child && !census && !wall && detection.format < 0)
            return;
        int const first = !records.empty() && records.front().king ? w.round % 4 : 0;
        bool const ownBeacon = !records.empty() && records.front().king && records.front().id == w.myId &&
                               w.round >= beaconStart();
        bool const flood = !records.empty() && records.front().census && records.front().cen.flood;
        int slot = 0;
        bool censusSent = false;
        bool detectionDue = detection.format >= 0 && !ownBeacon && !flood &&
                            (listen::teamOf(detection) != listen::teamOf(detectionSent) ||
                             w.round - detectionSentRound >= P.gossipMaxAge);
        for (int i = 0; i < 4; i++) {
            int const dir = (first + i) % 4;
            if (probes[dir])
                continue;
            if (dir == inherit) {
                sending[dir] = true;
                out[dir] = listen::encodeDetection(detection, w.round, b.context());
                continue;
            }
            if (wall && dir == wall->first) {
                sending[dir] = true;
                out[dir] = wall->second;
                continue;
            }
            if (!rayUseful(w, origin, dir, backDir))
                continue;
            if (detectionDue && !(child && dir == backDir)) {
                sending[dir] = true;
                out[dir] = listen::encodeDetection(detection, w.round, b.context());
                detectionSent = detection;
                detectionSentRound = w.round;
                detectionDue = false;
                continue;
            }
            bool const spare = !ownBeacon && !flood && slot >= static_cast<int>(records.size());
            bool const takesCensus = spare && census && (!censusSent || census->second.cen.flood);
            if (records.empty() && !(child && dir == backDir) && !takesCensus)
                continue;
            Record rec = child && dir == backDir ? *child
                       : ownBeacon || flood       ? records.front()
                       : takesCensus              ? census->second
                                                  : records[slot++ % records.size()];
            if (rec.claim) {
                sending[dir] = true;
                out[dir] = encodeClaim(*claim, b.context());
                claimSent = *claim;
                claimSentRound = w.round;
                continue;
            }
            censusSent = censusSent || rec.census;
            if (P.shareSymmetry && P.useSymmetry && rec.countdown)
                rec.symmetry = w.symmetry;
            sending[dir] = true;
            out[dir] = P.shareKelpEdges && !kelp.seen.empty() ? kelp.attach(w, rec, b.context())
                                                           : encodeRecord(rec, b.context());
            if (rec.king)
                b.markKingSent(rec);
            else if (rec.census)
                b.census.markSent(rec.cen);
            else if (rec.countdown)
                b.spawnSent[rec.where] = rec.round;
            else
                b.blockSent[rec.where] = rec.round;
        }
    }

    // Sends the planned plain values, sealed for this round (wire::seal).
    void emit(unswbc::Controller& ct, int round, wire::Context const& ctx) const {
        for (int dir = 0; dir < 4; dir++)
            if (sending[dir])
                ct.send_sonar(unswbc::Direction(DIR_CHAR[dir]), wire::seal(out[dir], round, ctx));
    }
};

} // namespace bot
