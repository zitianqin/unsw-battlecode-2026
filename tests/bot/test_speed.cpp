// The speed-ups must not change a decision: each cache or shortcut here is checked against the plain
// computation it replaces, on hand-made boards and on seeded random ones (kelp, portals, bodies, heads).
#include "belief.hpp"
#include "gossip.hpp"
#include "safety.hpp"
#include "support.hpp"
#include "targets.hpp"

#include <algorithm>
#include <cstdint>
#include <deque>
#include <vector>

using test::require;

namespace {

// A small deterministic generator, so a failing board can be rebuilt.
struct Lcg {
    std::uint32_t state;
    int below(int n) {
        state = state * 1664525u + 1013904223u;
        return static_cast<int>((state >> 8) % static_cast<std::uint32_t>(n));
    }
};

// Checks step(), openRow and jumpSides against computeStep() for every side of every tile.
void requireStepCache(bot::World const& w) {
    for (int t = 0; t < w.N; t++) {
        for (int d = 0; d < 4; d++) {
            int const to = w.computeStep(t, d);
            require(w.step(t, d) == to, "the step cache must match the edge memory");
            if (!w.rowBits())
                continue;
            bool const plain = to == w.neighbour(t, d);
            require((w.openRow[d][w.yOf(t)] >> w.xOf(t) & 1) == (plain ? 1u : 0u),
                    "a row bit is set exactly for a plain step");
            bool const listed = std::find(w.jumpSides.begin(), w.jumpSides.end(), 4 * t + d) != w.jumpSides.end();
            require(listed == (to >= 0 && !plain), "jumpSides lists exactly the steps through a paired portal");
        }
    }
}

// Remembers random edges: open, kelp and portals whose ids repeat, so some pair up and some are seen again.
void rememberRandomEdges(bot::World& w, Lcg& rng, int count) {
    for (int i = 0; i < count; i++) {
        int const e = rng.below(2 * w.N);
        int const kind = rng.below(10);
        if (kind < 2)
            w.rememberEdge(e, unswbc::Edge(e < w.N, unswbc::EdgeType::PORTAL, rng.below(6)));
        else if (kind < 5)
            w.rememberEdge(e, unswbc::Edge(e < w.N, unswbc::EdgeType::KELP));
        else
            w.rememberEdge(e, unswbc::Edge(e < w.N, unswbc::EdgeType::EMPTY));
    }
}

void step_cache_follows_every_edge_change() {
    for (auto [width, height] : {std::pair{13, 9}, std::pair{64, 5}, std::pair{70, 3}, std::pair{1, 4}}) {
        auto w = test::world(width, height);
        Lcg rng{static_cast<std::uint32_t>(width * 131 + height)};
        requireStepCache(w);
        for (int round = 0; round < 12; round++) {
            rememberRandomEdges(w, rng, 25);
            requireStepCache(w);
        }
        w.setAllEdges(bot::EDGE_KELP);
        requireStepCache(w);
    }
}

// Scatters kelp, portal pairs and dragons (heads, bodies with and without known tail distances) on w.
void clutter(bot::World& w, Lcg& rng) {
    rememberRandomEdges(w, rng, w.N / 2);
    w.touched.clear();
    w.dragons.clear();
    for (int id = 1; id <= 8; id++) {
        int const head = rng.below(w.N);
        if (w.occId[head] >= 0)
            continue;
        bool const enemy = id % 2 == 0;
        if (id != w.myId)
            test::dragon(w, id, enemy, head, 2 + rng.below(5));
        int cur = head;
        for (int k = 0; k < 4; k++) {
            int const next = w.step(cur, rng.below(4));
            if (next < 0 || w.occId[next] >= 0)
                break;
            w.occId[next] = id;
            w.occEnemy[next] = enemy;
            w.occK[next] = rng.below(3) == 0 ? -1 : k;
            w.touched.push_back(next);
            cur = next;
        }
    }
}

void bitset_head_search_matches_the_queue() {
    for (auto [width, height] : {std::pair{64, 64}, std::pair{64, 7}, std::pair{17, 5}, std::pair{9, 1}, std::pair{1, 9}, std::pair{30, 80}}) {
        for (std::uint32_t seed = 1; seed <= 6; seed++) {
            auto w = test::world(width, height);
            Lcg rng{seed * 7919u + static_cast<std::uint32_t>(width)};
            clutter(w, rng);
            w.length = 2 + rng.below(30);
            bot::Safety rows, queue;
            rows.prepare(w);
            queue.prepare(w);
            int const limit = bot::fillCap(w);
            std::fill(queue.otherDist.begin(), queue.otherDist.end(), -1);
            queue.seedOtherQueue(w);
            for (int layer = 1; layer <= limit && queue.otherQueueLayer(w, layer); layer++) {
            }
            for (int t = 0; t < w.N; t++)
                require(rows.otherHeadDist(t) == (queue.otherDist[t] < 0 ? bot::NEVER : queue.otherDist[t]),
                        "the row search must find the same head distances as the queue search");
        }
    }
}

// The other-head search spelled out plainly: one queue from every other visible head, entering only tiles that
// free up, out to fillCap steps. -1 where no head arrives in time.
std::vector<int> plainHeadSearch(bot::World const& w) {
    std::vector<int> dist(w.N, -1);
    std::deque<int> open;
    for (auto const& d : w.dragons) {
        if (d.id != w.myId && dist[d.head] < 0) {
            dist[d.head] = 0;
            open.push_back(d.head);
        }
    }
    for (; !open.empty(); open.pop_front()) {
        int const cur = open.front();
        for (int dir = 0; dir < 4; dir++) {
            int const to = w.step(cur, dir);
            if (to >= 0 && dist[to] < 0 && w.freeAt(to, 0) < bot::NEVER && dist[cur] < bot::fillCap(w)) {
                dist[to] = dist[cur] + 1;
                open.push_back(to);
            }
        }
    }
    return dist;
}

// The flood fill asks otherReachesWithin as it goes, growing the head search only as deep as it has looked;
// whatever the order of questions, each answer must be the complete search's.
void lazy_head_search_answers_like_the_full_one() {
    for (auto [width, height] : {std::pair{64, 64}, std::pair{40, 23}, std::pair{70, 9}, std::pair{9, 1}}) {
        for (std::uint32_t seed = 1; seed <= 4; seed++) {
            auto w = test::world(width, height);
            Lcg rng{seed * 104729u + static_cast<std::uint32_t>(height)};
            clutter(w, rng);
            w.length = 2 + rng.below(40);
            bot::Safety full, lazy;
            full.prepare(w);
            lazy.prepare(w);
            for (int ask = 0; ask < 3 * w.N; ask++) {
                int const t = rng.below(w.N);
                int const steps = rng.below(bot::fillCap(w) + 5);
                require(lazy.otherReachesWithin(t, steps) == (full.otherHeadDist(t) <= steps),
                        "a partly grown search must answer as the complete one does");
            }
            std::vector<int> const plain = plainHeadSearch(w);
            for (int t = 0; t < w.N; t++) {
                require(lazy.otherHeadDist(t) == full.otherHeadDist(t), "grown to the end, both searches agree");
                require(full.otherHeadDist(t) == (plain[t] < 0 ? bot::NEVER : plain[t]),
                        "and they agree with the plain search");
            }
        }
    }
}

void head_search_reaches_exactly_the_fill_cap() {
    auto w = test::world(64, 64);
    int const enemy = w.tileOf(10, 10);
    test::dragon(w, 2, true, enemy, 3);
    w.length = 2;
    bot::Safety safety;
    safety.prepare(w);
    require(bot::fillCap(w) == 40, "a short dragon's fill cap is P.spaceCap");
    require(safety.otherHeadDist(enemy) == 0, "an other head is its own source");
    require(safety.otherHeadDist(w.tileOf(30, 30)) == 40, "a tile 40 steps away is within the search");
    require(safety.otherHeadDist(w.tileOf(31, 30)) == bot::NEVER, "a tile 41 steps away is past it");
    require(safety.otherHeadDist(w.tileOf(60, 10)) == 14, "the search wraps round the torus");

    test::wall(w, enemy, 1);
    int const body = w.tileOf(10, 11);
    w.occId[body] = 2;
    w.occEnemy[body] = 1;
    w.touched.push_back(body);
    safety.prepare(w);
    require(safety.otherHeadDist(w.tileOf(11, 10)) == 3, "kelp east of the head forces a detour north");
    require(safety.otherHeadDist(body) == bot::NEVER, "a body with no known tail is never entered");
}

void target_candidates_cover_the_torus_diamond() {
    int const D = P.maxTargetDist;
    int const size = 2 * D + 10; // wider than the diamond, so only its tiles are examined
    auto w = test::world(size, size);
    w.head = w.tileOf(1, 1);
    int const near = w.tileOf(size - 6, D - 6);     // 7 west across the edge, D - 7 south: D steps
    int const far = w.tileOf(size - 7, D - 6);      // D + 1 steps
    int const corner = w.tileOf(1, size + 1 - D);   // D north across the edge
    for (int t : {near, far, corner})
        test::pearl(w, t);
    for (int x : {D + 6, D + 7, D + 8})
        test::fast(w, w.tileOf(x, 1)); // a farm D + 3 to D + 5 steps away round the torus, worth the trip
    bot::Targets targets;
    targets.gatherCandidates(w);
    auto const has = [&](int t) {
        return std::any_of(targets.candidates.begin(), targets.candidates.end(),
                           [&](auto const& c) { return c.tile == t; });
    };
    require(has(near) && has(corner), "pearls P.maxTargetDist steps away count, whichever edge they wrap across");
    require(!has(far), "a pearl one step further is past P.maxTargetDist");
    require(has(w.tileOf(D + 6, 1)) && has(w.tileOf(D + 8, 1)), "fast tiles past the diamond still count");
    require(std::is_sorted(targets.candidates.begin(), targets.candidates.end(),
                           [](auto const& a, auto const& b) {
                               return a.roughCost != b.roughCost ? a.roughCost < b.roughCost : a.tile < b.tile;
                           }),
            "candidates come nearest first, ties by tile");
}

void fast_tiles_are_listed_once() {
    auto w = test::world(20, 20);
    int const t = w.tileOf(5, 5);
    for (int round = 10; round < 14; round++) {
        w.round = round;
        w.spawnKind[t] = bot::SPAWN_YES;
        w.spawnRound[t] = round;
        w.noteCountdown(t, 1);
    }
    require(w.fastTile(t), "four resets to 1 make a fast tile");
    require(std::count(w.resetTiles.begin(), w.resetTiles.end(), t) == 1, "a fast tile is listed exactly once");
}

void pruned_pearl_search_keeps_the_winner_field() {
    auto w = test::world(30, 30);
    w.head = w.tileOf(10, 10);
    // Two steps away as the crow flies, six round its kelp: examined first, it sets the cost to beat.
    int const boxed = w.tileOf(12, 10);
    for (int dir : {0, 2, 3})
        test::wall(w, boxed, dir);
    // Three steps as the crow flies, five round one kelp edge: it wins by a step, so the pruned search
    // must still reach it.
    int const pearl = w.tileOf(10, 13);
    test::wall(w, pearl, 0);
    for (int t : {boxed, pearl})
        test::pearl(w, t);
    bot::Targets targets;
    targets.gatherCandidates(w);
    require(targets.candidates.size() == 2 && targets.candidates[0].tile == boxed, "the boxed pearl looks nearer");
    targets.choosePearl(w);
    require(targets.pearl == pearl && targets.pearlField.at(w.head) == 5, "the pearl five steps away wins");
    bot::DistField full;
    full.build(w, pearl, P.maxTargetDist + 1, w.head);
    for (int t = 0; t < w.N; t++)
        require(targets.pearlField.at(t) == full.at(t), "the winner's field matches a search to its full reach");
}

void explore_field_is_exact_beside_our_head() {
    auto w = test::world(32, 32);
    Lcg rng{99};
    rememberRandomEdges(w, rng, 600);
    w.head = w.tileOf(16, 16);
    for (int t = 0; t < w.N; t++)
        w.visStamp[t] = 0;
    bot::Targets targets;
    targets.chooseExplore(w);
    require(targets.explore >= 0, "an unseen board gives an explore target");
    bot::DistField full;
    full.build(w, targets.explore, w.W + w.H);
    for (int dir = 0; dir < 4; dir++) {
        int const dest = w.step(w.head, dir);
        if (dest >= 0)
            require(targets.exploreField.at(dest) == full.at(dest), "scoring reads the same explore distances");
    }
}

void explore_field_reaches_the_far_side_of_our_head() {
    auto w = test::world(32, 32);
    w.head = w.tileOf(16, 16);
    for (int t = 0; t < w.N; t++)
        w.visStamp[t] = 0;
    bot::Targets targets;
    targets.explore = w.tileOf(16, 6);
    targets.exploreChosenRound = w.round;
    targets.chooseExplore(w);
    require(targets.explore == w.tileOf(16, 6), "a fresh explore target is kept");
    require(targets.exploreField.at(w.step(w.head, 0)) == 9 && targets.exploreField.at(w.step(w.head, 2)) == 11,
            "the step away from the target is one past our head, and still measured");
}

void gossip_sends_countdowns_until_they_age_out() {
    auto w = test::world();
    bot::Belief b;
    b.init(w);
    bot::Record heard;
    heard.countdown = true;
    heard.round = 100;
    heard.where = w.tileOf(3, 3);
    heard.wait = 40;
    b.merge(heard, 100);
    auto const sends = [&](int round) {
        w.round = round;
        b.observe(w);
        for (auto const& r : bot::Gossip::gather(w, b))
            if (r.countdown && r.where == heard.where)
                return true;
        return false;
    };
    require(sends(100 + P.gossipMaxAge), "a countdown is worth relaying while it is P.gossipMaxAge old");
    require(!sends(101 + P.gossipMaxAge), "one round older it is not");
    heard.round = 131;
    heard.wait = 5;
    b.merge(heard, 131);
    require(sends(131), "a fresher report of the same tile is sent again");
}

} // namespace

int main() {
    return test::suite({
        {"step cache follows every edge change", step_cache_follows_every_edge_change},
        {"bitset head search matches the queue", bitset_head_search_matches_the_queue},
        {"lazy head search answers like the full one", lazy_head_search_answers_like_the_full_one},
        {"head search reaches exactly the fill cap", head_search_reaches_exactly_the_fill_cap},
        {"target candidates cover the torus diamond", target_candidates_cover_the_torus_diamond},
        {"fast tiles are listed once", fast_tiles_are_listed_once},
        {"pruned pearl search keeps the winner field", pruned_pearl_search_keeps_the_winner_field},
        {"explore field is exact beside our head", explore_field_is_exact_beside_our_head},
        {"explore field reaches the far side of our head", explore_field_reaches_the_far_side_of_our_head},
        {"gossip sends countdowns until they age out", gossip_sends_countdowns_until_they_age_out},
    });
}
