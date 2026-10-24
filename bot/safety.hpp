#pragma once

#include "harvest.hpp"
#include "notes.hpp"
#include "state.hpp"

#include <algorithm>
#include <bit>
#include <cstdint>
#include <vector>

namespace bot {

// Variants of the flood fill (see Safety::floodFill).
struct FillOptions {
    bool visibleOnly = false;          // drop tiles out of sight: the pessimistic view of a pocket
    bool staticBodies = false;         // every body tile is a wall, for checks where body timings fail
    bool ownBodyOnly = false;          // only walls and our own body block (a split child can't walk back through
                                       // our body; other dragons move on): the pocket the map and we make
    DistField const* rival = nullptr;  // extra heads to lose ties to (the other half of a split, or enemy heads)
    bool physical = false;             // ignore other heads' territory: only walls and timed bodies block
    int wall = -1;                     // one more tile that blocks (a sprint's middle tile, now our neck)
    bool headReachBlocks = false;      // beyond the first tile, tiles another visible head can step onto are walls:
                                       // every other dragon moves once before our next move and may take them
};

// The flood fill's cap for a move (evaluateMove, farmEntryOk): P.spaceCap tiles, or P.spaceCapPerLength per
// segment if more.
inline int fillCap(World const& w) { return std::max(P.spaceCap, P.spaceCapPerLength * w.length); }

// The most tiles a survival check asks of a move's region before calling it open: fillCap, or with
// P.roomByRoundsLeft at most the tiles the game's remaining moves can use, the landing and one per move after it
// (the game ends after round MAX_ROUNDS - 1), so a pocket that holds the dragon to the end is open.
inline int holdCap(World const& w) {
    return P.roomByRoundsLeft ? std::min(fillCap(w), unswbc::Constants::MAX_ROUNDS - w.round) : fillCap(w);
}

// True when enemy head d's visible chain ends in a tail we can confirm (World::interior: every neighbour in view),
// so its visibleLen is its length; otherwise the body may run on out of sight.
inline bool lengthKnown(World const& w, SeenDragon const& d) {
    int last = d.head;
    for (int k = 1; k < d.visibleLen && w.behind[last] >= 0; k++)
        last = w.behind[last];
    return w.interior(last);
}

// Survival checks for one candidate move: the timing-aware flood fill, which tiles an enemy head can hit
// before our next move, and whether a head-on trade is worth it. Build once per turn with prepare().
struct Safety {
    World const* w = nullptr;
    std::vector<int> seen;        // BFS visit stamps
    int seenStamp = 0;
    std::vector<int> queue;
    std::vector<int> depth;
    std::vector<int> delay;           // pearls eaten along the fill's path to a tile (our tail waits one each)
    std::vector<int> otherDist;       // steps for the nearest other head (either team) to reach a tile, -1 none
    std::vector<int> enemyReachStamp; // == w->stamp when an enemy head can step here on its next move
    std::vector<int> headReachStamp;  // == w->stamp when any other visible head can step here on its next move
    std::vector<std::uint64_t> freeRow, visitedRow, frontierRow, nextRow; // bit rows for otherRowsLayer
    std::uint64_t frontierRows = 0;   // bit y set when frontierRow[y] is not empty (maps at most 64 tall)
    std::vector<int> otherQueue;      // otherQueueLayer's queue: [otherAt, otherTail) waits to be expanded
    std::size_t otherAt = 0, otherTail = 0;
    int otherLayer = 0;               // otherDist is complete up to this many steps ...
    int otherLimit = 0;               // ... and the search stops at this many (fillCap)
    bool otherDone = false;           // no layer beyond otherLayer reaches a new tile
    bool reachedFreed = false;        // set by floodFill: it entered a tile some body is vacating
    DistField rivalField;             // scratch for split checks
    DistField enemyField;             // scratch for the split's territory: steps for the nearest enemy head
    bool protectedDragon = false;     // late game and plausibly our longest: refuses every trade
    int headEdge = 0;                 // visible friendly heads (ours included) minus visible enemy heads

    void prepare(World const& world) {
        w = &world;
        if (seen.empty()) {
            seen.assign(world.N, 0);
            depth.assign(world.N, 0);
            delay.assign(world.N, 0);
            enemyReachStamp.assign(world.N, 0);
            headReachStamp.assign(world.N, 0);
            otherDist.assign(world.N, -1);
            queue.reserve(world.N);
        }
        for (auto const& d : world.dragons) {
            if (d.id == world.myId)
                continue;
            for (int dir = 0; dir < 4; dir++) {
                int const to = world.step(d.head, dir);
                if (to < 0)
                    continue;
                headReachStamp[to] = world.stamp;
                if (d.enemy)
                    enemyReachStamp[to] = world.stamp;
            }
        }
        buildOtherHeadDist(world);
        int longestFriend = 0;
        headEdge = 0;
        for (auto const& d : world.dragons) {
            headEdge += d.enemy ? -1 : 1;
            if (!d.enemy && d.id != world.myId)
                longestFriend = std::max(longestFriend, d.visibleLen);
        }
        protectedDragon = world.round >= P.mergeRound && world.length >= P.protectMinLength &&
                          world.length >= longestFriend;
    }

    // Multi-source BFS from every other visible head, so the flood fill only counts tiles we reach first.
    // Heads are sources; any other tile is entered only if it frees up (freeAt < NEVER). It searches as deep
    // as a flood fill of fillCap() can ask about: floodFill compares only steps up to its cap. prepare() only
    // seeds it; growOther() adds layers as a fill first asks about them, so a turn pays only for the depth its
    // fills reach, and every layer comes out as a whole search would find it.
    void buildOtherHeadDist(World const& world) {
        std::fill(otherDist.begin(), otherDist.end(), -1);
        otherLimit = fillCap(world);
        otherLayer = 0;
        otherDone = false;
        if (world.rowBits())
            seedOtherRows(world);
        else
            seedOtherQueue(world);
    }

    // Completes layers up to `depth` (at most the search's limit).
    void growOther(int depth) {
        depth = std::min(depth, otherLimit);
        while (!otherDone && otherLayer < depth) {
            otherLayer++;
            otherDone = w->rowBits() ? !otherRowsLayer(*w, otherLayer) : !otherQueueLayer(*w, otherLayer);
        }
    }

    void seedOtherQueue(World const& world) {
        otherQueue.resize(world.N); // scratch: every tile enters at most once
        otherAt = otherTail = 0;
        int* const dist = otherDist.data();
        for (auto const& d : world.dragons) {
            if (d.id == world.myId || dist[d.head] >= 0)
                continue;
            dist[d.head] = 0;
            otherQueue[otherTail++] = d.head;
        }
    }

    // Expands the queued tiles of layer - 1 into `layer`; false once nothing is left to expand.
    bool otherQueueLayer(World const& world, int layer) {
        int* const q = otherQueue.data();
        int* const dist = otherDist.data();
        int const* const steps = world.stepTo.data();
        for (; otherAt < otherTail && dist[q[otherAt]] == layer - 1; otherAt++) {
            int const* const out = steps + 4 * q[otherAt];
            for (int dir = 0; dir < 4; dir++) {
                int const to = out[dir];
                if (to < 0 || dist[to] >= 0 || world.freeAt(to, 0) >= NEVER)
                    continue;
                dist[to] = layer;
                q[otherTail++] = to;
            }
        }
        return otherAt < otherTail;
    }

    // The same search a whole layer at a time over bit rows (World::openRow), for maps at most 64 wide.
    // Blocked tiles come from World::touched, which lists every occupied tile.
    void seedOtherRows(World const& world) {
        int const H = world.H;
        freeRow.assign(H, world.rowMask());
        visitedRow.assign(H, 0);
        frontierRow.assign(H, 0);
        nextRow.assign(H, 0);
        for (int t : world.touched)
            if (world.occId[t] >= 0 && world.freeAt(t, 0) >= NEVER)
                freeRow[world.yOf(t)] &= ~(1ULL << world.xOf(t));
        frontierRows = 0;
        int* const dist = otherDist.data();
        for (auto const& d : world.dragons) {
            if (d.id == world.myId || dist[d.head] >= 0)
                continue;
            dist[d.head] = 0;
            std::uint64_t const bit = 1ULL << world.xOf(d.head);
            visitedRow[world.yOf(d.head)] |= bit;
            frontierRow[world.yOf(d.head)] |= bit;
            frontierRows |= 1ULL << (world.yOf(d.head) & 63);
        }
    }

    // Fills in `layer` from the previous layer's frontier; false when it reached no new tile. Only rows beside a
    // frontier row, or holding a portal landing, can gain tiles; on maps at most 64 tall only those are visited.
    bool otherRowsLayer(World const& world, int layer) {
        int const W = world.W, H = world.H;
        std::uint64_t const full = world.rowMask();
        int* const dist = otherDist.data();
        std::uint64_t const* const north = world.openRow[0].data();
        std::uint64_t const* const east = world.openRow[1].data();
        std::uint64_t const* const south = world.openRow[2].data();
        std::uint64_t const* const west = world.openRow[3].data();
        std::uint64_t const* const from = frontierRow.data();
        std::uint64_t* const to = nextRow.data();
        std::uint64_t* const visited = visitedRow.data();
        std::uint64_t const* const open = freeRow.data();
        std::uint64_t landings = 0;
        for (int side : world.jumpSides) {
            int const t = side >> 2;
            if (from[world.yOf(t)] >> world.xOf(t) & 1) {
                int const landing = world.stepTo[side];
                to[world.yOf(landing)] |= 1ULL << world.xOf(landing);
                landings |= 1ULL << (world.yOf(landing) & 63);
            }
        }
        std::uint64_t any = 0, reachedRows = 0;
        auto const row = [&](int y) {
            int const up = y == 0 ? H - 1 : y - 1, down = y == H - 1 ? 0 : y + 1;
            std::uint64_t const above = from[up], here = from[y], below = from[down];
            std::uint64_t reached = to[y];
            if (above | here | below) {
                std::uint64_t const e = here & east[y], w = here & west[y];
                reached |= ((e << 1 | e >> (W - 1)) | (w >> 1 | w << (W - 1))) & full;
                reached |= (below & north[down]) | (above & south[up]);
            }
            reached &= open[y] & ~visited[y];
            to[y] = reached;
            visited[y] |= reached;
            any |= reached;
            if (reached)
                reachedRows |= 1ULL << (y & 63);
            for (; reached; reached &= reached - 1)
                dist[y * W + std::countr_zero(reached)] = layer;
        };
        if (H <= 64) {
            std::uint64_t const rows = H == 64 ? ~0ULL : (1ULL << H) - 1;
            std::uint64_t const f = frontierRows;
            std::uint64_t todo = (f | ((f << 1 | f >> (H - 1)) & rows) | ((f >> 1 | f << (H - 1)) & rows) | landings);
            for (; todo; todo &= todo - 1)
                row(std::countr_zero(todo));
        } else {
            for (int y = 0; y < H; y++)
                row(y);
        }
        if (!any)
            return false;
        std::swap(frontierRow, nextRow);
        std::fill(nextRow.begin(), nextRow.end(), 0);
        frontierRows = reachedRows;
        return true;
    }

    // Steps for the nearest other head to reach t (NEVER if none can within the search). Completes the search.
    int otherHeadDist(int t) {
        growOther(otherLimit);
        return otherDist[t] < 0 ? NEVER : otherDist[t];
    }

    // True when some other head reaches t in at most `steps` steps (otherHeadDist(t) <= steps), searching
    // only that deep.
    bool otherReachesWithin(int t, int steps) {
        growOther(steps);
        return otherDist[t] >= 0 && otherDist[t] <= steps;
    }

    bool enemyCanReach(int t) const { return enemyReachStamp[t] == w->stamp; }

    // Reachable tiles from `start`, entered on step 1, counting each tile at the earliest step its occupant
    // has moved off. Stops at `cap`. selfDelay = 1 when step 1 eats a pearl; each visible pearl further along
    // the path holds our tail back one more turn.
    // With P.floodTerritoryOnly (and not options.physical), a tile counts only if we get there strictly
    // before every other head: a soft measure of our share, since that head may well go elsewhere. That
    // check is exact for caps up to fillCap(), the deepest buildOtherHeadDist searches.
    // reachedFreed says whether the region reaches a tile a tail is leaving: then we can follow it forever.
    // With kNotes, notes.fill says whose tiles those were and whether the fill crossed an unseen edge.
    // A region that neither reaches `cap` nor a freed tile is a closed pocket (a dead-end corridor, say):
    // a snake cannot turn round in it, however many tiles it counts.
    // FillOptions gives the pessimistic, static-body and rival-head variants.
    int floodFill(int start, int selfDelay, int cap, FillOptions const& options = {}) {
        seenStamp++;
        queue.clear();
        queue.push_back(start);
        seen[start] = seenStamp;
        depth[start] = 1;
        delay[start] = selfDelay;
        int count = 1;
        reachedFreed = false;
        if constexpr (kNotes)
            notes.fill = {};
        for (std::size_t at = 0; at < queue.size() && count < cap; at++) {
            int const cur = queue[at];
            int const next = depth[cur] + 1;
            for (int dir = 0; dir < 4; dir++) {
                int const to = w->step(cur, dir);
                if (to < 0 || seen[to] == seenStamp)
                    continue;
                if (options.ownBodyOnly ? w->occId[to] == w->myId
                                        : w->freeAt(to, delay[cur]) > next || (options.staticBodies && w->occId[to] >= 0))
                    continue;
                if (to == options.wall)
                    continue;
                if (options.headReachBlocks && headReachStamp[to] == w->stamp)
                    continue;
                if (P.floodTerritoryOnly && !options.physical && otherReachesWithin(to, next))
                    continue;
                if (options.visibleOnly && !w->visible(to) && w->occId[to] != w->myId)
                    continue;
                if (options.rival && next >= options.rival->at(to))
                    continue;
                seen[to] = seenStamp;
                depth[to] = next;
                delay[to] = delay[cur] + (w->hasPearlHere(to) ? 1 : 0);
                queue.push_back(to);
                if (w->occId[to] >= 0)
                    reachedFreed = true;
                if constexpr (kNotes) {
                    if (w->occId[to] >= 0)
                        (w->occId[to] == w->myId ? notes.fill.freedOwn : notes.fill.freedOther) = true;
                    notes.fill.unseenEdge = notes.fill.unseenEdge || w->edgeKind[w->edgeOf(cur, dir)] == EDGE_UNKNOWN;
                }
                if (++count >= cap)
                    break;
            }
        }
        return count;
    }

    // True when losing this dragon for `theirLen` of theirs is worth it.
    bool tradeGood(int theirLen, int minGain) const {
        return !protectedDragon && w->unitCount >= P.minUnitsToTrade && theirLen - w->length >= minGain;
    }

    // True when ramming an enemy head of `theirLen` is worth starting by the hand rule: the ram gate (ramWorth, for
    // findRam and the one-step attack) with P.ramModel off, except even rams under P.evenTradeHarvest, and the hunt's
    // choice of longer prey.
    // Both heads die and the side with more heads nearby eats most of the dropped pearls, so the local head
    // edge sets the price: an even trade needs headEdge >= P.evenTradeMinEdge, and while outnumbered in our
    // window (headEdge < 0) the victim must be at least P.outnumberedAttackMinGain longer.
    bool attackGood(int theirLen) const {
        int const gain = theirLen - w->length;
        if (headEdge < 0 && gain < P.outnumberedAttackMinGain)
            return false;
        return tradeGood(theirLen, P.attackMinGain) && (gain > 0 || headEdge >= P.evenTradeMinEdge);
    }

    // True when enemy head d ramming us, after we take `moves` (our steps from here; the head-on happens where they
    // end), would be a bad trade for us. With P.pricedGuard it is priced as findRam prices our own rams (rammodel.hpp's
    // ramWorth), from d's side, at our length there (a pearl eaten on the way adds one, each step after the first
    // costs one): a head always when we are protected, the team is too small to trade (Safety::tradeGood's checks) or
    // it is more than -P.ramModelMinGain shorter; a head as long as us (visible) when it eats the drop, with
    // P.evenTradeHarvest (dropHarvestEdge, ours at most -P.evenTradeMinHarvest); any other when the ram model's swing
    // of its ram on us (headOnSwing, our tile as the victim) is above P.ramModelMin.
    // Without it the hand rule: a head the trade leaves shorter than P.riskTradeMinGain above us and, with
    // P.outnumberedRamGuard, while outnumbered in view (headEdge <= P.ramGuardMaxEdge) an even or longer head too.
    // visibleLen is a lower bound, so "at least as long" never mistakes a shorter head for one.
    bool badRammer(SeenDragon const& d, std::vector<int> moves) const {
        if (!P.pricedGuard) {
            bool const outnumbered = P.outnumberedRamGuard && headEdge <= P.ramGuardMaxEdge;
            return !tradeGood(d.visibleLen, P.riskTradeMinGain) || (outnumbered && d.visibleLen >= w->length);
        }
        int at = w->head, length = w->length;
        for (std::size_t j = 0; j < moves.size(); j++) {
            at = w->step(at, moves[j]);
            length += (at >= 0 && w->hasPearlHere(at) ? 1 : 0) - (j >= 1 ? 1 : 0);
        }
        if (protectedDragon || w->unitCount < P.minUnitsToTrade || d.visibleLen - length < P.ramModelMinGain)
            return true;
        if (P.evenTradeHarvest && d.visibleLen == length) {
            moves.push_back(0); // their step into our head, which moves nothing of ours
            return dropHarvestEdge(*w, d.head, moves) <= -P.evenTradeMinHarvest;
        }
        return headOnSwing(*w, true, d.head, d.visibleLen, at, length, -headEdge) > P.ramModelMin;
    }

    // True when some enemy head that could ram tile t (a step from our head) next would be a bad trade for us
    // (badRammer). Only one-step reach.
    bool riskyFor(int t) const {
        if (!enemyCanReach(t))
            return false;
        std::vector<int> moves;
        for (int dir = 0; dir < 4 && moves.empty(); dir++)
            if (w->step(w->head, dir) == t)
                moves.push_back(dir);
        for (auto const& d : w->dragons) {
            if (!d.enemy)
                continue;
            bool adjacent = false;
            for (int dir = 0; dir < 4; dir++)
                adjacent = adjacent || w->step(d.head, dir) == t;
            if (adjacent && badRammer(d, moves))
                return true;
        }
        return false;
    }

    // True when an enemy head strictly longer than us (visible) can step onto t next.
    bool longerEnemyNext(int t) const {
        for (auto const& d : w->dragons)
            for (int dir = 0; dir < 4 && d.enemy && d.visibleLen > w->length; dir++)
                if (w->step(d.head, dir) == t)
                    return true;
        return false;
    }

    // True when this move occupies a visible teammate's only escape (P.guardTeammateExit): it would die on
    // its next move. Our tail can provide another exit if this move does not eat; an unknown portal also
    // remains a possible way out.
    bool blocksTeammateExit(int dest, bool eats) const {
        if (!P.guardTeammateExit)
            return false;
        for (auto const& d : w->dragons) {
            if (d.enemy || d.id == w->myId)
                continue;
            bool entersExit = false;
            bool otherExit = false;
            for (int dir = 0; dir < 4; dir++) {
                int const to = w->step(d.head, dir);
                if (to == dest) {
                    entersExit = true;
                } else if (to == UNKNOWN_PORTAL ||
                           (to >= 0 && (w->occId[to] < 0 ||
                                        (!eats && w->occId[to] == w->myId && w->occK[to] == 0)))) {
                    otherExit = true;
                }
            }
            if (entersExit && !otherExit)
                return true;
        }
        return false;
    }
};

} // namespace bot
