#pragma once

#include "harvest.hpp"
#include "safety.hpp"
#include "state.hpp"

#include <algorithm>
#include <array>
#ifdef __wasm__
#include <chrono>
#endif
#include <cstdint>
#include <utility>
#include <vector>

namespace bot {

// What the local search found for each first step: the first depth (our moves in the line) at which the enemy heads
// near us can force our loss after it, 0 when they can't within `depth`, the deepest search that finished.
struct SearchVerdict {
    int depth = 0; // 0: no verdict (no enemy head near, or the first depth ran out of work)
    std::array<int, 4> lostAt{};
};

// Short local search over the enemy heads near us (P.localSearch): our next moves against every reply of the bad
// rammers (Safety::badRammer) that could land on our head within it. It runs for dragons at least P.searchMinLength
// long, or with P.searchOnSwing while one of those heads' ram on us would swing more than P.searchMinSwing its way
// (the ram model's headOnSwing, harvest.hpp): what the ram costs us, net of what the rammer loses, beats the food that
// steering clear gives up. The
// one-step checks see only an enemy head's next step (Safety::riskyFor), while most rams on our long dragons are
// 2-3-step sprints.
//
// Nothing moves during our turn, so after each of our moves every searched head replies once before our next move
// (higher ids later this round, lower ids at the start of the next), in that order. Its ram may be any legal move
// onto our head: a step, or with P.searchSprints a sprint of up to length - 1 steps, each step past the first paid
// with a segment and each pearl eaten on the way (a visible pearl, or a fast tile out of sight) paying one back, as
// the engine orders it (docs/unsw/execution-order.md), through known portals as World::step goes. Its other replies
// are a step or a split (its head stays, shorter), or with P.searchSprintMoves any paid sprint too: a sprint that
// rams costs the rammer nothing it would keep, one that only closes in costs it length. An enemy whose tail we can't
// confirm (lengthKnown) is taken as at least P.maxRamSteps + 1 long, as enemyReach does. Each tile a reply can end
// on is searched once, with the most length left there; the tiles it walked stay its body as far as its new length
// reaches back. Teammates and the other heads stand still, and bodies free on their usual schedule (World::freeAt).
//
// It is a worst-case proof search, not a scoring. A line is lost when a searched head lands on our head
// (P.searchRamLoss), when we have no legal step (a ram we'd start only counts if Safety::attackGood), or, with
// P.searchBoxLoss, when at the horizon our physical flood fill is short of evaluateMove's need: the heads box us in
// (without P.searchRamLoss every enemy head in view is searched, for that). An unknown portal counts as a way out.
// Our own moves are single steps.
//
// It deepens one move at a time up to P.searchMaxDepth while its work (the tiles its reply searches expand and its
// fills visit, and our nodes) stays under P.searchWorkLimit, and in the judge's wasm build its CPU under
// P.searchClockLimit points as a backstop (the judge's steady_clock advances one nanosecond a point:
// docs/unsw/libraries.md). Native builds (our local games) read no clock, since there it is real time and would make
// a game depend on how busy the host is; a depth that runs out gives no verdict, and the last finished one stands.
class LocalSearch {
public:
    // Searches every first step `searched` asks about (a first step that is lethal or a head-on is never searched:
    // evaluateMove scores those).
    SearchVerdict run(World const& world, Safety const& safety, std::array<bool, 4> const& searched) {
        SearchVerdict verdict;
        w = &world;
        s = &safety;
        work = 0;
        if (static_cast<int>(enterDist.size()) != w->N) {
            enterDist.assign(w->N, 0);
            enterStamp.assign(w->N, 0);
        }
        runStamp++;
        landFor = -1;
        aborted = false;
#ifdef __wasm__
        start = std::chrono::steady_clock::now();
#endif
        if (!P.localSearch || (!P.searchOnSwing && w->length < P.searchMinLength) || !pickMovers())
            return verdict;
        if (static_cast<int>(laid.size()) != w->N) {
            laid.assign(w->N, 0);
            fillSeen.assign(w->N, 0);
            replySeen.assign(w->N, 0);
            replyIndex.assign(w->N, -1);
            bestLen.assign(w->N, 0);
            parent.assign(w->N, -1);
            ring.assign(4 * w->N + 8, 0); // each tile settles once and pushes at most 4
        }
        head = w->head;
        delay = 0;
        for (int depth = 1; depth <= P.searchMaxDepth; depth++) {
            horizon = depth;
            std::array<int, 4> lostAt = verdict.lostAt;
            bool unlost = false;
            for (int dir = 0; dir < 4; dir++) {
                int const to = w->step(w->head, dir);
                if (!searched[dir] || lostAt[dir] || to < 0 || moverAt(to) >= 0 || w->freeAt(to, 0) > 1)
                    continue;
                if (ourStep(to, 1))
                    lostAt[dir] = depth;
                if (aborted)
                    return verdict;
                unlost = unlost || !lostAt[dir];
            }
            verdict.depth = depth;
            verdict.lostAt = lostAt;
            if (!unlost)
                break;
        }
        return verdict;
    }

    int workDone() const { return work; }

private:
    struct Mover {
        int tile = -1;
        int len = 0;      // its length, at least P.maxRamSteps + 1 when its tail is unconfirmed
        int seen = 0;     // its visible length, which our own ram judges (as evaluateMove does)
        bool bad = false; // its ram on us is a bad trade for us (Safety::badRammer, priced where our head stands now)
        bool alive = true;
    };

    // A tile a mover's reply can end on, with the most length left there; `from` indexes the reply it came
    // through (-1: straight from the head, -2: a split, which keeps the head's tile).
    struct Reply {
        int tile = -1;
        int len = 0;
        int from = -1;
    };

    World const* w = nullptr;
    Safety const* s = nullptr;
    std::vector<Mover> movers;          // in the order they move after us
    std::vector<std::uint8_t> laid;     // tiles the search's paths (ours and the movers') made body, counted
    std::vector<int> fillSeen, fillQueue, fillDepth;
    std::vector<Reply> replyStack;      // each replies() frame's reply tiles, stacked
    std::vector<int> orderStack;        // ... and the order it tries them in
    std::vector<int> replySeen;         // == replyStamp: gatherReplies reached the tile ...
    std::vector<int> replyIndex, bestLen, parent; // ... its replyStack index once settled (-1 before), the most
                                        // length found there so far, and the tile it came from
    std::vector<int> ring;              // gatherReplies' queue, longest first: a ring of `ringFront` .. `ringBack`
    std::size_t ringFront = 0, ringBack = 0;
    std::vector<int> enterDist, enterStamp; // beyond's cache: steps to the nearest portal side, this run's stamp
    int runStamp = 0;
    int landFor = -1, landDist = 0;     // ... and the nearest landing's steps to tile landFor
    int pearls = 0;                     // tiles that may pay a sprint step (pearlFor): the view's pearls and fast tiles
    int replyStamp = 0;
    int fillStamp = 0;
    int head = -1;    // our head in the search
    int delay = 0;    // pearls we ate in the search: our tail waits one each
    int horizon = 1;  // our moves in a line at the current depth
    int work = 0;
    bool aborted = false;
#ifdef __wasm__
    std::chrono::steady_clock::time_point start;
#endif

    // The searched heads: with P.searchRamLoss the bad rammers (Safety::badRammer) whose head is close enough to land
    // on ours within the search (reach), else every enemy head in view; the nearest P.searchMaxMovers of them (then
    // by id), put in the order they move after us. The others stand still. False when there is none, or with
    // P.searchOnSwing when no such head's ram on us swings more than P.searchMinSwing its way.
    bool pickMovers() {
        int constexpr minSize = unswbc::Constants::MIN_SIZE;
        movers.clear();
        pearls = static_cast<int>(w->resetTiles.size());
        for (int dy = -unswbc::Constants::VISION_RADIUS; dy <= unswbc::Constants::VISION_RADIUS; dy++)
            for (int dx = -unswbc::Constants::VISION_RADIUS; dx <= unswbc::Constants::VISION_RADIUS; dx++) {
                int const t = w->tileOf((w->xOf(w->head) + dx + w->W) % w->W, (w->yOf(w->head) + dy + w->H) % w->H);
                pearls += w->visible(t) && w->hasPearlHere(t) && !w->fastTile(t) ? 1 : 0;
            }
        std::vector<std::pair<SeenDragon const*, int>> near; // with its length
        for (auto const& d : w->dragons) {
            if (!d.enemy || d.head < 0)
                continue;
            int const len = lengthKnown(*w, d) ? d.visibleLen : std::max(d.visibleLen, P.maxRamSteps + 1);
            if (P.searchRamLoss &&
                (!s->badRammer(d, {}) || beyond(d.head, w->head, 2 * P.searchMaxDepth + len - minSize + pearls)))
                continue;
            near.push_back({&d, len});
        }
        auto const worthIt = [&](auto const& d) {
            return headOnSwing(*w, true, d.first->head, d.first->visibleLen, w->head, w->length, -s->headEdge) >
                   P.searchMinSwing;
        };
        if (near.empty() || (P.searchOnSwing && std::none_of(near.begin(), near.end(), worthIt)))
            return false;
        auto const dist = [&](auto const& d) { return w->torusDist(w->head, d.first->head); };
        std::sort(near.begin(), near.end(), [&](auto const& a, auto const& b) {
            return dist(a) != dist(b) ? dist(a) < dist(b) : a.first->id < b.first->id;
        });
        if (static_cast<int>(near.size()) > P.searchMaxMovers)
            near.resize(P.searchMaxMovers);
        auto const order = [&](auto const& d) { return d.first->id > w->myId ? d.first->id : d.first->id + (1 << 20); };
        std::sort(near.begin(), near.end(), [&](auto const& a, auto const& b) { return order(a) < order(b); });
        for (auto const& [d, len] : near)
            movers.push_back({d->head, len, d->visibleLen, s->badRammer(*d, {}), true});
        return true;
    }

    // True when a is more than `limit` steps from b: a lower bound on those steps is the torus distance, or, through
    // known portals, the steps to the nearest portal side, one across it and the steps from the nearest landing
    // (World::jumpSides; entrance distances are cached for the turn). On a map wider than 64, which keeps no
    // jumpSides, a known portal leaves no bound.
    bool beyond(int a, int b, int limit) {
        if (w->torusDist(a, b) <= limit)
            return false;
        if (!w->rowBits())
            return w->portalEdges.empty();
        if (w->jumpSides.empty())
            return true;
        if (enterStamp[a] != runStamp) {
            spend(static_cast<int>(w->jumpSides.size()));
            enterStamp[a] = runStamp;
            enterDist[a] = NEVER;
            for (int side : w->jumpSides)
                enterDist[a] = std::min(enterDist[a], w->torusDist(a, side >> 2));
        }
        if (landFor != b) {
            spend(static_cast<int>(w->jumpSides.size()));
            landFor = b;
            landDist = NEVER;
            for (int side : w->jumpSides)
                landDist = std::min(landDist, w->torusDist(w->stepTo[side], b));
        }
        return enterDist[a] + 1 + landDist > limit;
    }

    int moverAt(int t) const {
        for (std::size_t i = 0; i < movers.size(); i++)
            if (movers[i].alive && movers[i].tile == t)
                return static_cast<int>(i);
        return -1;
    }

    // Adds to the work done; false (and aborted) once it passes P.searchWorkLimit or, in the judge's wasm build only,
    // the turn's search passes P.searchClockLimit points.
    bool spend(int units) {
        work += units;
        if (work > P.searchWorkLimit)
            aborted = true;
#ifdef __wasm__
        else if ((work >> 10) != ((work - units) >> 10) &&
                 std::chrono::steady_clock::now() - start > std::chrono::nanoseconds(P.searchClockLimit))
            aborted = true;
#endif
        return !aborted;
    }

    // A pearl a mover would eat on t: one in view, or a fast tile out of sight (it is probably full).
    bool pearlFor(int t) const { return w->visible(t) ? w->hasPearlHere(t) : w->fastTile(t); }

    // Our step to the free tile `to` as our move number `move`, then the enemy replies. True when the enemy heads can
    // force our loss from there.
    bool ourStep(int to, int move) {
        int const from = head;
        int const oldDelay = delay;
        laid[from]++;
        head = to;
        if (w->hasPearlHere(to))
            delay++;
        bool const lost = replies(move, 0);
        head = from;
        delay = oldDelay;
        laid[from]--;
        return lost;
    }

    // Our move number `move`: true when every step loses (or none is legal).
    bool ourMove(int move) {
        if (!spend(1))
            return false;
        std::array<int, 4> dirs{0, 1, 2, 3};
        std::array<int, 4> away{};
        for (int dir = 0; dir < 4; dir++) {
            int const to = w->step(head, dir);
            away[dir] = to < 0 ? 0 : nearestMover(to);
        }
        std::stable_sort(dirs.begin(), dirs.end(), [&](int a, int b) { return away[a] > away[b]; });
        for (int dir : dirs) {
            int const to = w->step(head, dir);
            if (to == UNKNOWN_PORTAL)
                return false;
            if (to < 0)
                continue;
            if (int const m = moverAt(to); m >= 0) {
                if (s->attackGood(movers[m].seen))
                    return false;
                continue;
            }
            if (laid[to] || w->freeAt(to, delay) > move)
                continue;
            if (!ourStep(to, move) || aborted)
                return false;
        }
        return true;
    }

    // Mover `i` replies after our move number `move`: true when some reply of it (with the later movers' and our
    // later moves) forces our loss.
    bool replies(int move, std::size_t i) {
        if (aborted)
            return false;
        if (i == movers.size())
            return move >= horizon ? leafLost(move) : ourMove(move + 1);
        if (move >= horizon && !P.searchBoxLoss)
            return lastRams(move, i);
        Mover& m = movers[i];
        if (!m.alive)
            return replies(move, i + 1);
        int const base = static_cast<int>(replyStack.size());
        if (gatherReplies(m, move) || aborted) {
            bool const rammed = !aborted;
            replyStack.resize(base);
            return rammed;
        }
        int const count = static_cast<int>(replyStack.size()) - base;
        int const orderBase = static_cast<int>(orderStack.size());
        for (int k = 0; k < count; k++)
            orderStack.push_back(base + k);
        std::stable_sort(orderStack.begin() + orderBase, orderStack.end(), [&](int a, int b) {
            return w->torusDist(replyStack[a].tile, head) < w->torusDist(replyStack[b].tile, head);
        });
        int const from = m.tile;
        int const len = m.len;
        bool lost = false;
        for (int k = 0; k < count && !lost && !aborted; k++) {
            Reply const r = replyStack[orderStack[orderBase + k]];
            layPath(from, r, 1);
            m.tile = r.tile;
            m.len = r.len;
            lost = replies(move, i + 1);
            m.len = len;
            m.tile = from;
            layPath(from, r, -1);
        }
        replyStack.resize(base);
        orderStack.resize(orderBase);
        if (count > 0 || aborted)
            return lost && !aborted;
        m.alive = false; // no legal reply: it dies
        lost = replies(move, i + 1);
        m.alive = true;
        return lost;
    }

    // The horizon's last replies when only rams lose a line: true when mover `i` or a later one can land on our
    // head. Their other replies can't lose it, so they aren't tried (and don't block each other's way).
    bool lastRams(int move, std::size_t i) {
        for (; i < movers.size() && !aborted; i++) {
            if (!movers[i].alive || !movers[i].bad)
                continue;
            int const base = static_cast<int>(replyStack.size());
            bool const rammed = gatherReplies(movers[i], move);
            replyStack.resize(base);
            if (rammed)
                return !aborted;
        }
        return false;
    }

    // Pushes onto replyStack every tile mover m can end a reply on after our move number `move`, each with the most
    // length it can have left there, the way it came by linked through Reply::from. True when it can land on our
    // head and that loses the line (P.searchRamLoss and a bad rammer); our head is never a reply. A step past the
    // first needs length >= 3 before it (a length-2 dragon can't pay) and costs one; a pearl on the tile adds one.
    // Lengths only fall by one a step past the first, so a 0-1 search (longest first) settles each tile once, with
    // the most length left. A bad rammer's ram may be any paid sprint; a reply that doesn't ram is a step or a split
    // unless P.searchSprintMoves lets it sprint too.
    bool gatherReplies(Mover const& m, int move) {
        int const base = static_cast<int>(replyStack.size());
        int constexpr minSize = unswbc::Constants::MIN_SIZE;
        if (m.len >= 2 * minSize) // a split leaves both halves at least minSize long
            replyStack.push_back({m.tile, m.len - minSize, -2});
        bool const rams = P.searchRamLoss && m.bad;
        bool const sprints = P.searchSprints && (rams || P.searchSprintMoves);
        replyStamp++;
        replySeen[m.tile] = replyStamp; // its neck once it moves
        replyIndex[m.tile] = -1;
        bestLen[m.tile] = NEVER;
        ringFront = ringBack = 0;
        pushBack(m.tile);
        while (ringFront != ringBack) {
            int const cur = ring[ringFront];
            ringFront = (ringFront + 1) % ring.size();
            bool const fromHead = cur == m.tile;
            int const len = fromHead ? m.len : bestLen[cur];
            if (!fromHead) {
                if (replyIndex[cur] >= 0)
                    continue; // settled already, with more length
                replyIndex[cur] = static_cast<int>(replyStack.size());
                replyStack.push_back({cur, len, replyIndex[parent[cur]]});
                if (!sprints || len <= minSize) // a paid step needs a segment to spare
                    continue;
                if (!P.searchSprintMoves && beyond(cur, head, len - minSize + 1 + pearls))
                    continue; // only a ram goes on sprinting, and it can't reach our head from here
            }
            if (!spend(4))
                return false;
            for (int dir = 0; dir < 4; dir++) {
                int const to = w->step(cur, dir);
                if (to < 0)
                    continue;
                if (to == head) {
                    if (rams)
                        return true;
                    continue;
                }
                int const next = len + (pearlFor(to) ? 1 : 0) - (fromHead ? 0 : 1);
                if (replySeen[to] == replyStamp) {
                    if (replyIndex[to] >= 0 || next <= bestLen[to])
                        continue;
                } else if (laid[to] || moverAt(to) >= 0 || w->freeAt(to, delay) > move + 1) {
                    continue;
                }
                replySeen[to] = replyStamp;
                replyIndex[to] = -1;
                bestLen[to] = next;
                parent[to] = cur;
                if (next > len || (next == len && !fromHead)) { // no shorter than cur: next up
                    ringFront = (ringFront + ring.size() - 1) % ring.size();
                    ring[ringFront] = to;
                } else {
                    pushBack(to);
                }
            }
        }
        if (!P.searchSprintMoves) { // keep the steps and the split: every entry left links to none
            int keep = base;
            for (int k = base; k < static_cast<int>(replyStack.size()); k++)
                if (replyStack[k].from < 0)
                    replyStack[keep++] = replyStack[k];
            replyStack.resize(keep);
        }
        return false;
    }

    void pushBack(int t) {
        ring[ringBack] = t;
        ringBack = (ringBack + 1) % ring.size();
    }

    // Adds `on` (1 or -1) to laid on the tiles a reply's head leaves behind as body, nearest its end first, as many as
    // its new length less the head covers: the way back to its start `from`, then `from` itself.
    void layPath(int from, Reply const& r, int on) {
        if (r.from == -2)
            return; // a split: the head stays
        int keep = r.len - 1;
        int k = r.from;
        for (; k >= 0 && keep > 0; k = replyStack[k].from, keep--)
            laid[replyStack[k].tile] += on;
        if (k < 0 && keep > 0)
            laid[from] += on;
    }

    // Steps from t to the nearest live mover's head (torus distance), NEVER with none.
    int nearestMover(int t) const {
        int best = NEVER;
        for (auto const& m : movers)
            if (m.alive)
                best = std::min(best, w->torusDist(t, m.tile));
        return best;
    }

    // At the horizon, after our move number `move` and the replies: with P.searchBoxLoss, true when the tiles we can
    // still reach (walls, timed bodies, the search's paths and the movers' heads blocking) are fewer than
    // evaluateMove's need (length + pearls eaten + P.spaceMargin, at most holdCap), counting our head.
    bool leafLost(int move) {
        if (!P.searchBoxLoss)
            return false;
        int const need = std::min(w->length + delay + P.spaceMargin, holdCap(*w));
        fillStamp++;
        fillQueue.assign(1, head);
        fillDepth.assign(1, 0);
        fillSeen[head] = fillStamp;
        int count = 1;
        for (std::size_t at = 0; at < fillQueue.size() && count < need; at++) {
            int const cur = fillQueue[at];
            int const next = fillDepth[at] + 1;
            for (int dir = 0; dir < 4 && count < need; dir++) {
                int const to = w->step(cur, dir);
                if (to == UNKNOWN_PORTAL)
                    return false;
                if (to < 0 || fillSeen[to] == fillStamp || laid[to] || moverAt(to) >= 0 ||
                    w->freeAt(to, delay) > move + next)
                    continue;
                fillSeen[to] = fillStamp;
                fillQueue.push_back(to);
                fillDepth.push_back(next);
                count++;
            }
        }
        spend(static_cast<int>(fillQueue.size()) + 1);
        return count < need;
    }
};

// LocalSearch::run for this turn's world.
inline SearchVerdict searchMoves(World const& w, Safety const& safety, std::array<bool, 4> const& searched) {
    static LocalSearch search;
    return search.run(w, safety, searched);
}

} // namespace bot
