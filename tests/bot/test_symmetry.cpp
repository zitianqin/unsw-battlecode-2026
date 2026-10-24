// The map's symmetry (symmetry.hpp): which candidate mirror survives paired sightings, and what memory the
// mirror half gets once one is left. Each case feeds vision through World::observe from a hand-made symmetric
// map (Truth), as main does: observe, Belief::observe, readSymmetry.
#include "support.hpp"
#include "symmetry.hpp"
#include "targets.hpp"

#include <functional>
#include <map>
#include <utility>
#include <vector>

using test::require;

namespace {

// A symmetric map under candidate `k` (World::symLive): edges and countdowns set on one tile are set on its
// image too. Countdowns: spawn[t] is a fixed next spawn round (never, when absent), or `countdown` overrides.
struct Truth {
    bot::World geo; // geometry only
    int k;
    std::map<int, std::pair<unswbc::EdgeType, int>> edges; // canonical edge -> kind, portal id
    std::map<int, int> spawn;
    std::function<int(int, int)> countdown; // (tile, round) -> pearl time, when set

    Truth(int width, int height, int symmetry) : geo(test::world(width, height)), k(symmetry) {}

    void kelp(int x, int y, int dir, bool mirrored = true) {
        int const e = geo.edgeOf(geo.tileOf(x, y), dir);
        edges[e] = {unswbc::EdgeType::KELP, -1};
        if (mirrored)
            edges[geo.mirrorEdge(e, k)] = {unswbc::EdgeType::KELP, -1};
    }

    // A portal pair on side `dir` of (x1, y1) and (x2, y2), id `id`; its image pair gets id `imageId`.
    void portal(int x1, int y1, int x2, int y2, int dir, int id, int imageId) {
        int const a = geo.edgeOf(geo.tileOf(x1, y1), dir), b = geo.edgeOf(geo.tileOf(x2, y2), dir);
        edges[a] = edges[b] = {unswbc::EdgeType::PORTAL, id};
        edges[geo.mirrorEdge(a, k)] = edges[geo.mirrorEdge(b, k)] = {unswbc::EdgeType::PORTAL, imageId};
    }

    void spawnAt(int x, int y, int round) {
        int const t = geo.tileOf(x, y);
        spawn[t] = spawn[geo.mirrorTile(t, k)] = round;
    }

    int pearlTime(int t, int round) const {
        if (countdown)
            return countdown(t, round);
        auto const found = spawn.find(t);
        return found == spawn.end() ? -1 : found->second - round;
    }

    unswbc::Tile tile(int t, int round) const {
        std::array<unswbc::Edge, 4> sides{};
        for (int d = 0; d < 4; d++) {
            int const e = geo.edgeOf(t, d);
            auto const found = edges.find(e);
            sides[d] = found == edges.end() ? unswbc::Edge(d % 2 == 0, unswbc::EdgeType::EMPTY)
                       : found->second.first == unswbc::EdgeType::PORTAL
                           ? unswbc::Edge(d % 2 == 0, unswbc::EdgeType::PORTAL, found->second.second)
                           : unswbc::Edge(d % 2 == 0, found->second.first);
        }
        return {unswbc::Position{geo.xOf(t), geo.yOf(t)}, std::nullopt, pearlTime(t, round), false, sides};
    }

    // The whole map known, for the true path lengths.
    bot::World known() const {
        bot::World w = geo;
        for (auto const& [e, edge] : edges) {
            if (edge.first == unswbc::EdgeType::PORTAL)
                w.setEdge(e, bot::EDGE_PORTAL, edge.second);
            else
                w.setEdge(e, bot::EDGE_KELP);
        }
        return w;
    }
};

// One dragon's process on a Truth map, seeing whatever tiles a case names each round.
struct Dragon {
    unswbc::Game game;
    unswbc::Controller controller{1, unswbc::Team::A, unswbc::Direction::EAST, {}, 64};
    bot::World w;
    bot::Belief belief;

    explicit Dragon(Truth const& truth) : game(truth.geo.W, truth.geo.H, 64) {
        w.init(controller, game);
        belief.init(w);
    }

    // One turn at `round` seeing `tiles` (x, y); the head stands on the first.
    void look(Truth const& truth, int round, std::vector<std::pair<int, int>> const& tiles) {
        std::vector<unswbc::Tile> seen;
        for (auto [x, y] : tiles)
            seen.push_back(truth.tile(w.tileOf(x, y), round));
        game.round_num = round;
        controller.length = 1;
        controller.head.position = {tiles[0].first, tiles[0].second};
        controller.vision = unswbc::Vision(std::move(seen));
        w.observe(controller, game);
        belief.observe(w);
        bot::readSymmetry(w, belief);
    }

    int at(int x, int y) const { return w.tileOf(x, y); }
};

// The idea's map: 32 x 16, mirrored top to bottom (candidate 0: (x, y) -> (x, 15 - y)). Spawn tiles (5,3) and its
// image (5,12) fire at `far`; (26,3) and (26,12), the images of (5,3) under the other two candidates, fire a round
// earlier. Kelp: south of (7,4) (so north of (7,11)) and east of (3,5) (so east of (3,10)).
Truth idea(int far = 200) {
    Truth truth(32, 16, 0);
    truth.spawnAt(5, 3, far);
    truth.spawnAt(26, 3, far - 1);
    truth.kelp(7, 4, 2);
    truth.kelp(3, 5, 1);
    return truth;
}

// Sees (5,3), (5,12) and (26,12): only candidate 0 fits the countdowns.
void identify(Dragon& d, Truth const& truth, int round) {
    d.look(truth, round, {{5, 3}, {5, 12}, {26, 12}, {7, 4}});
}

void readings_of_one_countdown_agree_until_it_resets() {
    require(bot::sameCountdown(10, 30, 10, 30), "one reading of a shared countdown");
    require(bot::sameCountdown(10, 30, 25, 30), "a later reading before the spawn says the same round");
    require(!bot::sameCountdown(10, 30, 25, 31), "a later reading before the spawn that differs is another countdown");
    require(!bot::sameCountdown(25, 31, 10, 30), "the order of the readings does not matter");
    require(bot::sameCountdown(10, 30, 30, 44), "at its spawn round the countdown resets to a fresh draw");
}

void kelp_against_open_ground_drops_that_mirror_only() {
    Truth const truth = idea();
    Dragon d(truth);
    d.look(truth, 1, {{3, 5}, {28, 5}});
    require(!(d.w.symLive >> 1 & 1), "kelp east of (3,5) with open ground west of (28,5) rules out the left-right mirror");
    require((d.w.symLive & 5) == 5, "the other two candidates have no seen pair yet");
    require(d.w.symmetry < 0, "two candidates left: no symmetry yet");
    int const image = d.w.edgeOf(d.at(3, 10), 1);
    require(d.w.edgeKind[image] == bot::EDGE_UNKNOWN && d.w.step(d.at(3, 10), 1) == d.at(4, 10),
            "until one candidate remains nothing is written into the mirror half");
}

void countdowns_pick_the_mirror_and_memory_fills_its_half() {
    Truth const truth = idea();
    Dragon d(truth);
    identify(d, truth, 5);
    require(d.w.symLive == 1 && d.w.symmetry == 0, "(5,3) and (5,12) share a countdown, (26,12) does not");
    require(d.w.step(d.at(7, 11), 0) == bot::KELP && d.w.edgeMirrored[d.w.edgeOf(d.at(7, 11), 0)],
            "kelp seen south of (7,4) is written north of its unseen image (7,11)");
    require(d.w.step(d.at(3, 10), 1) == d.at(4, 10), "kelp we never saw has no image to write");
    int const image = d.at(26, 3);
    require(d.w.spawnKind[image] == bot::SPAWN_YES && d.w.spawnRound[image] == 199 && d.w.tileMirrored[image],
            "the unseen (26,3) shares (26,12)'s countdown");
    require(d.w.spawnKind[d.at(7, 11)] == bot::SPAWN_NEVER, "a tile that never spawns has an image that never does");
    require(d.w.lastSeen[image] < 0, "a mirrored tile is still unseen: pearls and bodies differ between the halves");

    d.look(truth, 6, {{26, 3}});
    require(!d.w.tileMirrored[image] && d.w.symmetry == 0, "seeing the image replaces the mirrored memory; it agrees");
}

void a_tile_on_the_mirror_line_pairs_its_own_sides() {
    Truth truth(31, 16, 0);
    truth.kelp(15, 4, 1); // east of the middle column; its west side stays open
    Dragon d(truth);
    d.look(truth, 1, {{15, 4}});
    require(!(d.w.symLive >> 1 & 1), "under the left-right mirror (15,4) is its own image, east side against west");
    require(d.w.symLive & 1, "top-bottom pairs (15,4) with the unseen (15,11)");
}

void a_pearl_behind_mirrored_kelp_gets_its_true_path_length() {
    Truth truth = idea();
    for (int y = 1; y <= 7; y++)
        truth.kelp(10, y, 1); // a wall east of x = 10, rows 1-14 once mirrored
    Dragon d(truth);
    std::vector<std::pair<int, int>> wall;
    for (int y = 1; y <= 7; y++)
        wall.push_back({10, y});
    d.look(truth, 4, wall);
    int const from = d.at(9, 11), to = d.at(12, 11);
    bot::DistField field;
    field.build(d.w, from, 40);
    require(field.at(to) == 3, "before the symmetry is known the unseen bottom half is assumed open");

    identify(d, truth, 5);
    bot::World const known = truth.known();
    bot::DistField truePath;
    truePath.build(known, from, 40);
    field.build(d.w, from, 40);
    require(truePath.at(to) > 3 && field.at(to) == truePath.at(to), "the mirrored wall gives the true detour");
}

void a_mirrored_countdown_becomes_a_pearl_target() {
    Truth truth = idea();
    truth.spawnAt(8, 2, 30);
    for (bool identified : {false, true}) {
        Dragon d(truth);
        if (identified)
            identify(d, truth, 5);
        d.look(truth, 6, {{8, 2}});
        d.look(truth, 29, {{8, 11}});
        bot::Targets targets;
        targets.team = &d.belief;
        targets.update(d.w);
        int const image = d.at(8, 13);
        if (identified)
            require(targets.pearl == image, "(8,2) fires at 30, so its image (8,13) two steps away does too");
        else
            require(targets.pearl != image, "without the symmetry (8,13)'s countdown is unknown");
    }
}

// (8,2) and its image (8,13) spawn every 30 rounds; we read (8,2) just before round 30 and just after.
void a_later_draw_does_not_hide_a_probable_pearl() {
    Truth truth = idea();
    int const tile = truth.geo.tileOf(8, 2), image = truth.geo.tileOf(8, 13);
    truth.countdown = [&, base = truth](int t, int round) {
        return t == tile || t == image ? (round / 30 + 1) * 30 - round : base.pearlTime(t, round);
    };
    Dragon d(truth);
    identify(d, truth, 5);
    d.look(truth, 29, {{8, 2}});
    d.look(truth, 31, {{8, 2}});
    require(d.w.spawnRound[tile] == 60 && d.w.spawnRound[image] == 30,
            "(8,13) fired unseen at 30: (8,2)'s next draw doesn't replace that countdown");
    d.look(truth, 31, {{8, 11}});
    bot::Targets targets;
    targets.team = &d.belief;
    targets.update(d.w);
    require(targets.pearl == image, "so (8,13) is still a probable pearl");
    d.look(truth, 31 + P.pearlMemoryRounds, {{8, 2}});
    require(d.w.spawnRound[image] == d.w.spawnRound[tile], "once that is stale the next draw is written");

    // The team's reading at the image works the same way for a tile we read ourselves.
    Dragon e(truth);
    identify(e, truth, 5);
    e.look(truth, 29, {{8, 2}});
    bot::Record r;
    r.countdown = true;
    r.round = 31;
    r.where = image;
    r.wait = truth.pearlTime(image, 31);
    e.belief.merge(r, 31);
    e.look(truth, 31, {{8, 4}});
    targets = bot::Targets{};
    targets.team = &e.belief;
    targets.update(e.w);
    require(targets.pearl == tile, "a teammate's later draw at (8,13) doesn't hide the pearl (8,2) probably holds");
}

void teammates_countdowns_test_the_candidates() {
    Truth const truth = idea();
    Dragon d(truth);
    // A teammate read (5,3) and (26,3) at round 2, and (5,12) at round 3 (all before they fire).
    auto hear = [&](int x, int y, int round) {
        bot::Record r;
        r.countdown = true;
        r.round = round;
        r.where = d.at(x, y);
        r.wait = truth.pearlTime(r.where, round);
        d.belief.merge(r, 4);
    };
    hear(5, 3, 2);
    hear(26, 3, 2);
    hear(5, 12, 3);
    d.look(truth, 4, {{16, 8}});
    require(d.w.symLive == 1 && d.w.symmetry == 0,
            "(5,3) and (5,12) read a round apart share a countdown; (26,3) against (5,3) rules out the left-right "
            "mirror, and against (5,12) the rotation");
    require(d.w.spawnRound[d.at(26, 12)] < 0, "heard countdowns are read at the image in targets, not written");
}

void a_contradiction_drops_every_mirrored_write() {
    Truth truth = idea();
    truth.kelp(20, 6, 1, false); // east of (20,6) but not of its image (20,9): the map is not what we took it for
    truth.spawnAt(8, 2, 30);
    Dragon d(truth);
    identify(d, truth, 5);
    d.look(truth, 6, {{20, 6}, {8, 2}});
    require(d.w.step(d.at(20, 9), 1) == bot::KELP && d.w.spawnRound[d.at(8, 13)] == 30, "written from candidate 0");

    d.look(truth, 7, {{20, 9}});
    require(d.w.symLive == 0 && d.w.symmetry < 0, "open ground east of (20,9) contradicts the last candidate");
    require(d.w.step(d.at(7, 11), 0) == d.at(7, 10) && d.w.edgeKind[d.w.edgeOf(d.at(7, 11), 0)] == bot::EDGE_UNKNOWN,
            "mirrored kelp is forgotten");
    require(d.w.spawnKind[d.at(8, 13)] == bot::SPAWN_UNKNOWN && d.w.spawnRound[d.at(8, 13)] < 0,
            "mirrored countdowns are forgotten");
    require(d.w.step(d.at(20, 6), 1) == bot::KELP && d.w.step(d.at(20, 9), 1) == d.at(21, 9), "what we saw stays");
}

void fast_tiles_watched_on_one_side_count_on_both() {
    Truth truth = idea();
    int const fastTile = truth.geo.tileOf(12, 4);
    truth.countdown = [&, base = truth](int t, int round) {
        if (t == fastTile || t == truth.geo.mirrorTile(fastTile, 0))
            return 1; // resets every round, to 1
        return base.pearlTime(t, round);
    };
    Dragon d(truth);
    identify(d, truth, 5);
    for (int round = 6; round < 6 + 1 + P.fastMinResets; round++)
        d.look(truth, round, {{12, 4}});
    int const image = d.at(12, 11);
    require(d.w.fastTile(fastTile), "watched resetting to 1 often enough");
    require(d.w.fastTile(image), "its unseen image resets with it");
    require(std::count(d.w.resetTiles.begin(), d.w.resetTiles.end(), image) == 1, "listed once among the fast tiles");
}

void a_portal_pair_maps_onto_its_image_pair() {
    Truth truth = idea();
    truth.portal(4, 2, 9, 6, 1, 3, 8); // east of (4,2) and of (9,6); images east of (4,13) and (9,9), id 8
    Dragon d(truth);
    d.look(truth, 4, {{4, 2}, {9, 6}});
    identify(d, truth, 5);
    bot::World const known = truth.known();
    int const image = d.at(4, 13);
    require(known.step(image, 1) == d.at(10, 9), "the true map: east through (4,13) comes out east of (9,9)");
    require(d.w.step(image, 1) == known.step(image, 1), "the unseen image pair is paired");

    d.look(truth, 6, {{4, 13}});
    require(d.w.step(image, 1) == known.step(image, 1) && d.w.edgePortal[d.w.edgeOf(image, 1)] == 8,
            "seeing one end with its real id keeps the pair");
}

// A valid countdown may teach the mirror even if its tile's countdown is already known locally.
void a_child_hears_the_mirror_on_its_first_turn() {
    Truth const truth = idea();
    Dragon d(truth);
    d.look(truth, 40, {{7, 4}, {5, 3}});
    require(d.w.symmetry < 0, "this view alone cannot identify the mirror");
    bot::Record rec;
    rec.countdown = true;
    rec.round = d.w.round;
    rec.where = d.at(5, 3);
    rec.wait = truth.pearlTime(rec.where, rec.round);
    rec.symmetry = 0;
    d.belief.hear(d.w, {bot::encodeRecord(rec, d.belief.context())});
    bot::readSymmetry(d.w, d.belief);
    if (P.shareSymmetry) {
        require(d.w.symmetry == 0, "the child's first inbox supplies its mirror");
        require(d.w.symLive == 7, "a claim does not erase other locally possible mirrors");
        require(d.w.step(d.at(7, 11), 0) == bot::KELP, "the child's own wall sighting is mirrored immediately");
    } else {
        require(d.w.symmetry < 0, "switch off ignores the claim");
    }
}

void a_shared_mirror_cannot_override_local_contradictions() {
    Truth const truth = idea();
    Dragon d(truth);
    d.look(truth, 40, {{3, 5}, {28, 5}});
    require(!(d.w.symLive & 2), "our wall and open sighting rule out the left-right mirror");
    bot::Record rec;
    rec.countdown = true;
    rec.round = d.w.round;
    rec.where = d.at(5, 3);
    rec.wait = truth.pearlTime(rec.where, rec.round);
    rec.symmetry = 1;
    d.belief.hear(d.w, {bot::encodeRecord(rec, d.belief.context())});
    bot::readSymmetry(d.w, d.belief);
    require(d.w.symmetry < 0 && d.belief.sharedSymmetry < 0, "contradicted claims are ignored");
}

void a_later_sighting_drops_a_shared_mirror_and_its_writes() {
    Truth truth = idea();
    truth.kelp(20, 6, 1, false);
    Dragon d(truth);
    d.look(truth, 40, {{20, 6}, {5, 3}});
    bot::Record rec;
    rec.countdown = true;
    rec.round = d.w.round;
    rec.where = d.at(5, 3);
    rec.wait = truth.pearlTime(rec.where, rec.round);
    rec.symmetry = 0;
    d.belief.hear(d.w, {bot::encodeRecord(rec, d.belief.context())});
    bot::readSymmetry(d.w, d.belief);
    require(!P.shareSymmetry || d.w.step(d.at(20, 9), 1) == bot::KELP, "the shared mirror initially writes kelp");
    d.look(truth, 41, {{20, 9}});
    require(!(d.w.symLive & 1) && d.w.symmetry != 0, "the new sighting rules out the shared candidate");
    require(!d.w.tileMirrored[d.at(5, 12)], "its mirrored countdown was removed");
    require(d.w.step(d.at(20, 6), 1) == bot::KELP, "the real sighting stays");
    rec.round = d.w.round;
    d.belief.hear(d.w, {bot::encodeRecord(rec, d.belief.context())});
    bot::readSymmetry(d.w, d.belief);
    require(d.w.symmetry != 0, "a repeat cannot reinstate the contradicted claim");
}

} // namespace

int main() {
    return test::suite({
        {"a child hears the mirror on its first turn", a_child_hears_the_mirror_on_its_first_turn},
        {"a shared mirror cannot override local contradictions", a_shared_mirror_cannot_override_local_contradictions},
        {"a later sighting drops a shared mirror and its writes", a_later_sighting_drops_a_shared_mirror_and_its_writes},
        {"readings of one countdown agree until it resets", readings_of_one_countdown_agree_until_it_resets},
        {"kelp against open ground drops that mirror only", kelp_against_open_ground_drops_that_mirror_only},
        {"countdowns pick the mirror and memory fills its half", countdowns_pick_the_mirror_and_memory_fills_its_half},
        {"a tile on the mirror line pairs its own sides", a_tile_on_the_mirror_line_pairs_its_own_sides},
        {"a pearl behind mirrored kelp gets its true path length", a_pearl_behind_mirrored_kelp_gets_its_true_path_length},
        {"a mirrored countdown becomes a pearl target", a_mirrored_countdown_becomes_a_pearl_target},
        {"a later draw does not hide a probable pearl", a_later_draw_does_not_hide_a_probable_pearl},
        {"teammates' countdowns test the candidates", teammates_countdowns_test_the_candidates},
        {"a contradiction drops every mirrored write", a_contradiction_drops_every_mirrored_write},
        {"fast tiles watched on one side count on both", fast_tiles_watched_on_one_side_count_on_both},
        {"a portal pair maps onto its image pair", a_portal_pair_maps_onto_its_image_pair},
    });
}
