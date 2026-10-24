#include "safety.hpp"
#include "scoring.hpp"
#include "support.hpp"

#include <algorithm>

using test::require;

namespace {

unswbc::Tile segment(int x, int y, int id, bool enemy, bool head,
                     unswbc::Direction direction = unswbc::Direction::EAST) {
    unswbc::Position const position{x, y};
    return {position, unswbc::DragonPart(position, id, enemy ? unswbc::Team::B : unswbc::Team::A,
                                      direction, head), -1};
}

// Feeds real controller snapshots through World::observe without protocol I/O.
struct Observations {
    unswbc::Game game{12, 12, 64};
    unswbc::Controller controller{1, unswbc::Team::A, unswbc::Direction::EAST, {}, 64};
    bot::World w;

    Observations() { w.init(controller, game); }

    void observe(int round, int length, unswbc::Position head, std::vector<unswbc::Tile> tiles) {
        game.round_num = round;
        controller.length = length;
        controller.head.position = head;
        controller.vision = unswbc::Vision(std::move(tiles));
        w.observe(controller, game);
    }
};

void torus_paths_wrap_both_axes() {
    auto w = test::world(5, 4);
    require(w.step(0, 0) == 15, "north from (0,0) must wrap to (0,3)");
    require(w.step(0, 3) == 4, "west from (0,0) must wrap to (4,0)");
    require(w.step(19, 1) == 15, "east from (4,3) must wrap to (0,3)");
    require(w.step(19, 2) == 4, "south from (4,3) must wrap to (4,0)");
    bot::DistField field;
    field.build(w, 0, 6);
    require(field.at(19) == 2, "opposite displayed corners are two torus steps apart");
    require(w.torusDist(0, 19) == 2, "rough distance must also use both wrapped axes");
}

void kelp_blocks_both_sides_and_forces_detour() {
    auto w = test::world(9, 9);
    int const from = w.tileOf(2, 2), to = w.tileOf(3, 2);
    test::wall(w, from, 1);
    require(w.step(from, 1) == bot::KELP, "kelp must block eastbound crossing");
    require(w.step(to, 3) == bot::KELP, "the same kelp edge must block westbound crossing");
    bot::DistField field;
    field.build(w, from, 6);
    require(field.at(to) == 3, "one blocked edge requires north/east/south or south/east/north");
}

void portal_pairing_waits_for_a_distinct_partner() {
    auto w = test::world(12, 12);
    int const from = w.tileOf(2, 2);
    auto const portal = unswbc::Edge(true, unswbc::EdgeType::PORTAL, 7);
    w.rememberEdge(w.edgeOf(from, 0), portal);
    w.rememberEdge(w.edgeOf(from, 0), portal);
    require(w.step(from, 0) == bot::UNKNOWN_PORTAL, "seeing one portal edge twice must not pair it with itself");
    w.rememberEdge(w.edgeOf(w.tileOf(6, 5), 0), portal);
    require(w.step(from, 0) == w.tileOf(6, 4), "north must land north of the partner edge");
    require(w.step(w.tileOf(2, 1), 2) == w.tileOf(6, 5), "south must land south of the partner edge");
    require(w.step(w.tileOf(6, 5), 0) == w.tileOf(2, 1), "the partner portal must lead back");
    bot::DistField field;
    field.build(w, from, 2);
    require(field.at(w.tileOf(6, 4)) == 1, "crossing a paired portal costs one step");
}

void vertical_portal_preserves_east_west_crossing() {
    auto w = test::world(12, 12);
    auto const portal = unswbc::Edge(false, unswbc::EdgeType::PORTAL, 8);
    w.rememberEdge(w.edgeOf(w.tileOf(2, 2), 3), portal);
    w.rememberEdge(w.edgeOf(w.tileOf(6, 5), 3), portal);
    require(w.step(w.tileOf(2, 2), 3) == w.tileOf(5, 5), "west must land west of the partner edge");
    require(w.step(w.tileOf(1, 2), 1) == w.tileOf(6, 5), "east must land east of the partner edge");
}

void unknown_edges_are_open_but_unknown_portals_are_not_paths() {
    auto w = test::world(9, 9);
    int const source = w.tileOf(4, 4);
    w.setAllEdges(bot::EDGE_KELP);
    w.setEdge(w.edgeOf(source, 0), bot::EDGE_UNKNOWN);
    w.rememberEdge(w.edgeOf(source, 1), unswbc::Edge(false, unswbc::EdgeType::PORTAL, 3));
    bot::DistField field;
    field.build(w, source, 4);
    require(field.at(w.tileOf(4, 3)) == 1, "unseen ordinary edges are explored as open");
    require(field.at(w.tileOf(5, 4)) == bot::DistField::FAR, "an unpaired portal must not pretend to be an ordinary edge");
}

void observation_preserves_memory_and_clears_old_snapshot() {
    Observations scene;
    unswbc::Tile pearl{{6, 4}, std::nullopt, 7, true};
    pearl.edges[0] = unswbc::Edge(true, unswbc::EdgeType::KELP);
    scene.observe(100, 2, {5, 5}, {segment(5, 5, 1, false, true), segment(4, 5, 1, false, false),
                                 segment(7, 5, 9, true, true), pearl});
    auto& w = scene.w;
    int const remembered = w.tileOf(6, 4), enemy = w.tileOf(7, 5);
    require(w.hasPearlHere(remembered), "the initial visible pearl must be in the snapshot");
    require(w.dragonAt(enemy) != nullptr, "the initial enemy head must be in the snapshot");

    scene.observe(101, 2, {6, 5}, {segment(6, 5, 1, false, true), segment(5, 5, 1, false, false)});
    require(!w.visible(remembered) && !w.hasPearlHere(remembered), "old vision must not remain current vision");
    require(w.pearlNow[remembered] == 0, "transient pearl presence must be cleared");
    require(w.lastSeen[remembered] == 100 && w.pearlSeen[remembered] == 100,
            "out-of-sight pearl history must survive the next observation");
    require(w.spawnKind[remembered] == bot::SPAWN_YES && w.spawnRound[remembered] == 107,
            "the absolute spawn countdown must survive leaving vision");
    require(w.step(remembered, 0) == bot::KELP, "static edge memory must survive leaving vision");
    require(w.occId[enemy] == -1 && w.dragonAt(enemy) == nullptr,
            "an unseen enemy must not remain a current head or occupied tile");
}

// A (1,1) tile reports countdown 1 every round: each sighting after the first watches it reset to 1.
void tile_seen_resetting_to_one_twice_is_fast() {
    Observations scene;
    int const tile = scene.w.tileOf(6, 4);
    auto sight = [&](int round, int countdown) {
        scene.observe(round, 2, {5, 5}, {segment(5, 5, 1, false, true), {{6, 4}, std::nullopt, countdown, false}});
    };
    sight(100, 1);
    require(!scene.w.fastTile(tile), "one countdown says nothing about the tile's gap");
    sight(101, 1);
    require(!scene.w.fastTile(tile), "one reset to 1 could be luck on a slow tile");
    sight(102, 1);
    require(scene.w.fastTile(tile), "resetting to 1 twice marks the tile fast");

    Observations slow;
    int const other = slow.w.tileOf(6, 4);
    for (auto [round, countdown] : {std::pair{100, 1}, {101, 6}, {107, 6}, {110, 3}, {113, 6}})
        slow.observe(round, 2, {5, 5}, {segment(5, 5, 1, false, true), {{6, 4}, std::nullopt, countdown, false}});
    require(slow.w.resetCount[other] == 3 && !slow.w.fastTile(other),
            "resets to 6 are slow, and a mid-countdown sighting (round 110) is not a reset");
}

void observing_empty_pearl_tile_supersedes_older_sighting() {
    Observations scene;
    scene.observe(100, 2, {5, 5}, {segment(5, 5, 1, false, true), {{6, 4}, std::nullopt, 7, true}});
    scene.observe(101, 2, {5, 5}, {segment(5, 5, 1, false, true), {{6, 4}, std::nullopt, -1, false}});
    auto const& w = scene.w;
    int const tile = w.tileOf(6, 4);
    require(!w.hasPearlHere(tile), "a visible empty tile must clear the old pearl");
    require(w.pearlSeen[tile] == 100 && w.lastSeen[tile] == 101,
            "latest visibility must distinguish an empty sighting from a pearl sighting");
    require(w.spawnKind[tile] == bot::SPAWN_NEVER, "a never-spawns observation must supersede a prior countdown");
}

void own_body_growth_retains_unseen_tail_and_split_trims_it() {
    Observations scene;
    scene.observe(100, 3, {4, 4}, {segment(4, 4, 1, false, true), segment(3, 4, 1, false, false),
                                 segment(2, 4, 1, false, false)});
    scene.observe(101, 4, {5, 4}, {segment(5, 4, 1, false, true), segment(4, 4, 1, false, false)});
    auto& w = scene.w;
    int const tail = w.tileOf(2, 4);
    require(w.body == std::deque<int>({53, 52, 51, 50}), "growth must retain the prior tail behind the new head");
    require(!w.visible(tail) && w.occId[tail] == w.myId, "the remembered unseen own tail must still block movement");
    require(w.freeAt(tail, 0) == 2, "our tail is still occupied on the immediate move");
    require(w.freeAt(tail, 1) == 3, "eating must defer our tail release by one move");
    require(w.freeAt(w.head, 0) == 5, "all four own segments must pass before the old head clears");

    scene.observe(102, 2, {6, 4}, {segment(6, 4, 1, false, true), segment(5, 4, 1, false, false)});
    require(w.body == std::deque<int>({54, 53}), "a shorter reported body must trim old tail segments");
    require(w.freeAt(tail, 0) == 0, "trimmed unseen tail occupancy must not leak into later turns");
}

void contradictory_visibility_rebuilds_own_body() {
    Observations scene;
    scene.observe(100, 3, {4, 4}, {segment(4, 4, 1, false, true), segment(3, 4, 1, false, false),
                                 segment(2, 4, 1, false, false)});
    scene.observe(101, 3, {5, 4}, {segment(5, 4, 1, false, true), segment(4, 4, 1, false, false),
                                 {{3, 4}, std::nullopt, -1}});
    require(scene.w.body == std::deque<int>({53, 52}), "visible emptiness must invalidate a contradicted remembered tail");
    require(scene.w.occId[scene.w.tileOf(3, 4)] == -1, "body tracking must not put a segment back on a visible empty tile");
}

void portal_crossing_links_a_discontinuous_body() {
    Observations scene;
    auto head = segment(8, 5, 1, false, true);
    auto tail = segment(2, 2, 1, false, false);
    tail.edges[1] = unswbc::Edge(false, unswbc::EdgeType::PORTAL, 11);
    head.edges[3] = unswbc::Edge(false, unswbc::EdgeType::PORTAL, 11);
    scene.observe(100, 2, {8, 5}, {head, tail});
    require(scene.w.body == std::deque<int>({68, 26}), "body links must follow portals rather than map adjacency");
    require(scene.w.freeAt(26, 0) == 2, "a tail across a portal must retain normal tail timing");
}

void other_tail_timing_requires_surrounding_visibility() {
    Observations scene;
    std::vector<unswbc::Tile> tiles;
    for (int y = 1; y <= 7; ++y) {
        for (int x = 1; x <= 7; ++x) {
            if (y == 5 && x >= 4 && x <= 6)
                tiles.push_back(segment(x, y, 9, true, x == 6));
            else if (x == 2 && y == 2)
                tiles.push_back(segment(x, y, 1, false, true));
            else
                tiles.emplace_back(unswbc::Position{x, y}, std::nullopt, -1);
        }
    }
    scene.observe(100, 2, {2, 2}, std::move(tiles));
    auto& w = scene.w;
    int const tail = w.tileOf(4, 5), middle = w.tileOf(5, 5), head = w.tileOf(6, 5);
    require(w.occK[tail] == 0 && w.occK[middle] == 1 && w.occK[head] == 2,
            "a fully surrounded visible tail must number its body towards the head");
    require(w.freeAt(tail, 0) > 1 && w.freeAt(middle, 0) == w.freeAt(tail, 0) + 1,
            "another tail must release before its next segment, never on the immediate move");
    require(w.freeAt(tail, 1) == w.freeAt(tail, 0), "our growth must not delay another dragon's tail");
    require(w.freeAt(head, 0) == bot::NEVER, "a visible enemy head must remain blocked despite its known tail distance");

    scene.observe(101, 2, {2, 2}, {segment(2, 2, 1, false, true), segment(6, 5, 9, true, true),
                                 segment(5, 5, 9, true, false)});
    require(w.occK[middle] == -1 && w.freeAt(middle, 0) == bot::NEVER,
            "the end of a partial visible chain must not be mistaken for a confirmed tail");
    require(w.dragonAt(head)->visibleLen == 2, "visible length must shrink with this turn's partial chain");
}

// A fresh split child of length 12 at (5,5) sees its whole 7x7 window (x, y 2..8). Its head chains to three
// segments heading south; the body then leaves the window at (5,9), (6,9) and comes back up column 6 and east
// along row 5 to the tail at (8,5), whose neighbour (9,5) is out of sight. Those six segments are ours but
// unchained, so their tail distance is unknown: the deepest untracked segment (body index 4) has k = 12 - 4 - 1
// = 7, so none of them is sure to be free before step k + 2 = 9.
std::vector<unswbc::Tile> coiledChild(bool tailInView) {
    using unswbc::Direction;
    std::vector<std::pair<unswbc::Position, Direction>> const ours = {
        {{5, 6}, Direction::NORTH}, {{5, 7}, Direction::NORTH}, {{5, 8}, Direction::NORTH},
        {{6, 8}, Direction::SOUTH}, {{6, 7}, Direction::SOUTH}, {{6, 6}, Direction::SOUTH},
        {{6, 5}, Direction::SOUTH}, {{7, 5}, Direction::WEST},  {{8, 5}, Direction::WEST}};
    std::vector<unswbc::Tile> tiles;
    int const reach = tailInView ? 9 : 8;
    for (int y = 2; y <= 8; ++y) {
        for (int x = 2; x <= reach; ++x) {
            auto const found = std::find_if(ours.begin(), ours.end(),
                                            [&](auto const& s) { return s.first.x == x && s.first.y == y; });
            if (x == 5 && y == 5)
                tiles.push_back(segment(x, y, 1, false, true, Direction::NORTH));
            else if (found != ours.end())
                tiles.push_back(segment(x, y, 1, false, false, found->second));
            else if (x == 2 && (y == 3 || y == 4))
                tiles.push_back(segment(x, y, 9, true, false, Direction::NORTH)); // an enemy body leaving view
            else
                tiles.emplace_back(unswbc::Position{x, y}, std::nullopt, -1);
        }
    }
    return tiles;
}

void fresh_child_blocks_its_unchained_coils() {
    Observations scene;
    scene.observe(400, 12, {5, 5}, coiledChild(false));
    auto& w = scene.w;
    require(w.body.size() == 4, "the head chains only to the three segments it can follow in view");
    for (auto [x, y] : {std::pair{6, 8}, {6, 7}, {6, 6}, {6, 5}, {7, 5}, {8, 5}}) {
        int const t = w.tileOf(x, y);
        require(w.occId[t] == w.myId && w.occK[t] < 0, "an unchained own segment has no known tail distance");
        require(w.freeAt(t, 0) == 9, "an unchained own segment stays blocked until the latest step it could free");
        require(w.freeAt(t, 1) == 10, "eating holds every own segment one step longer");
    }
    require(w.freeAt(w.tileOf(2, 4), 0) == bot::NEVER, "another dragon's unchained body is still never free");

    bot::Safety safety;
    safety.prepare(w);
    safety.floodFill(w.tileOf(5, 4), 0, w.N);
    for (auto [x, y] : {std::pair{6, 7}, {6, 6}, {6, 5}, {7, 5}}) {
        int const t = w.tileOf(x, y);
        require(safety.seen[t] != safety.seenStamp || safety.depth[t] >= 9,
                "the flood fill must not count our own coils as room before they can free");
    }
    require(bot::evaluateMove(w, safety, bot::Targets{}, 1).score == P.lethalScore,
            "stepping east onto our own coil is lethal");
    require(bot::bestMove(w, safety, bot::Targets{}).score > -5e5, "the child still has a safe move");
}

// With (9,5) in view the tail at (8,5) is confirmed (nothing points at it and every way in is visible), so the
// child numbers its own tail segments like another dragon's: the tail frees at step 2, as a tracked tail does.
void fresh_child_numbers_its_visible_tail() {
    Observations scene;
    scene.observe(400, 12, {5, 5}, coiledChild(true));
    auto& w = scene.w;
    require(w.occK[w.tileOf(8, 5)] == 0 && w.occK[w.tileOf(6, 8)] == 5,
            "the visible tail numbers its chain towards the head");
    require(w.freeAt(w.tileOf(8, 5), 0) == 2 && w.freeAt(w.tileOf(6, 5), 0) == 4,
            "confirmed own segments free on the usual schedule");

    Observations pair;
    pair.observe(10, 2, {5, 5}, {segment(5, 5, 1, false, true, unswbc::Direction::NORTH),
                                segment(5, 6, 1, false, false, unswbc::Direction::NORTH),
                                {{5, 4}, std::nullopt, -1}});
    require(pair.w.body.size() == 2 && pair.w.freeAt(pair.w.tileOf(5, 6), 0) == 2,
            "a length-2 child tracks its whole body and may follow its tail on step 2");
    require(pair.w.freeAt(pair.w.tileOf(5, 4), 0) == 0, "open ground next to a length-2 child stays free");
}

void distance_field_stops_at_permanently_blocked_segments() {
    auto w = test::world(9, 9);
    int const source = w.tileOf(2, 4), blocker = w.tileOf(3, 4), beyond = w.tileOf(4, 4);
    w.setAllEdges(bot::EDGE_KELP);
    w.setEdge(w.edgeOf(source, 1), bot::EDGE_OPEN);
    w.setEdge(w.edgeOf(blocker, 1), bot::EDGE_OPEN);
    test::dragon(w, 9, true, blocker, 1);
    bot::DistField field;
    field.build(w, source, 6);
    require(field.at(blocker) == 1, "a blocked head must still get an approach distance");
    require(field.at(beyond) == bot::DistField::FAR, "paths must not expand through a head");

    w.occHead[blocker] = 0;
    field.build(w, source, 6);
    require(field.at(beyond) == bot::DistField::FAR, "an unconfirmed body tail must also block expansion");
    w.occK[blocker] = 0;
    field.build(w, source, 6);
    require(field.at(beyond) == 2, "a known moving tail can be traversed by the optimistic distance field");
}

void distance_field_rebuild_drops_old_distances() {
    auto w = test::world(16, 16);
    bot::DistField field;
    field.build(w, w.tileOf(8, 8), 8);
    require(field.at(w.tileOf(12, 8)) == 4, "the first field must reach the distant tile");
    field.build(w, w.tileOf(1, 1), 1);
    require(field.at(w.tileOf(1, 1)) == 0 && field.at(w.tileOf(2, 1)) == 1,
            "the rebuilt field must use its new source");
    require(field.at(w.tileOf(12, 8)) == bot::DistField::FAR, "old reachable tiles must not survive a new field stamp");
    require(field.at(w.tileOf(3, 1)) == bot::DistField::FAR, "depth limits must exclude the next layer");
}

void distance_field_stop_tile_keeps_one_extra_layer() {
    auto w = test::world(16, 16);
    bot::DistField field;
    field.build(w, w.tileOf(8, 8), 12, w.tileOf(9, 8));
    require(field.at(w.tileOf(10, 8)) == 2, "stopping must retain one layer past the reached target");
    require(field.at(w.tileOf(11, 8)) == bot::DistField::FAR, "stopping must avoid expanding the extra layer");
}


// Crowding memory (targets.hpp): a teammate's segment is remembered where it was seen until that tile is seen
// without one; our own and enemy segments are not teammates.
void teammate_segments_are_remembered_until_seen_gone() {
    Observations scene;
    scene.observe(100, 2, {5, 5}, {segment(5, 5, 1, false, true), segment(4, 5, 1, false, false),
                                 segment(6, 6, 7, false, true), segment(6, 7, 7, false, false),
                                 segment(3, 3, 9, true, false)});
    auto& w = scene.w;
    require(w.friendBodySeen[w.tileOf(6, 6)] == 100 && w.friendBodySeen[w.tileOf(6, 7)] == 100,
            "a teammate's head and segment are remembered");
    require(w.friendBodySeen[w.tileOf(4, 5)] == -1 && w.friendBodySeen[w.tileOf(5, 5)] == -1,
            "our own body is not a teammate's");
    require(w.friendBodySeen[w.tileOf(3, 3)] == -1, "an enemy segment is not a teammate's");

    unswbc::Tile empty{{6, 6}, std::nullopt, -1, false};
    scene.observe(101, 2, {5, 5}, {segment(5, 5, 1, false, true), empty});
    require(w.friendBodySeen[w.tileOf(6, 6)] == -1, "a tile seen empty forgets the teammate");
    require(w.friendBodySeen[w.tileOf(6, 7)] == 100, "a tile out of sight keeps it");
}
} // namespace

int main() {
    return test::suite({
        {"teammate segments are remembered until seen gone", teammate_segments_are_remembered_until_seen_gone},
        {"torus paths wrap both axes", torus_paths_wrap_both_axes},
        {"kelp blocks both sides and forces a detour", kelp_blocks_both_sides_and_forces_detour},
        {"portal pairing waits for a distinct partner", portal_pairing_waits_for_a_distinct_partner},
        {"vertical portal preserves east/west crossing", vertical_portal_preserves_east_west_crossing},
        {"unknown edges and unknown portals differ", unknown_edges_are_open_but_unknown_portals_are_not_paths},
        {"observation preserves memory and clears old snapshot", observation_preserves_memory_and_clears_old_snapshot},
        {"tile seen resetting to 1 twice is fast", tile_seen_resetting_to_one_twice_is_fast},
        {"empty pearl observation supersedes old sighting", observing_empty_pearl_tile_supersedes_older_sighting},
        {"own body growth and split update unseen occupancy", own_body_growth_retains_unseen_tail_and_split_trims_it},
        {"contradictory vision rebuilds own body", contradictory_visibility_rebuilds_own_body},
        {"portal crossing links discontinuous body", portal_crossing_links_a_discontinuous_body},
        {"other tail timing requires surrounding visibility", other_tail_timing_requires_surrounding_visibility},
        {"fresh child blocks its unchained coils", fresh_child_blocks_its_unchained_coils},
        {"fresh child numbers its visible tail", fresh_child_numbers_its_visible_tail},
        {"distance field stops at permanent blockers", distance_field_stops_at_permanently_blocked_segments},
        {"distance field rebuild drops old distances", distance_field_rebuild_drops_old_distances},
        {"distance field stop tile retains one extra layer", distance_field_stop_tile_keeps_one_extra_layer},
    });
}
