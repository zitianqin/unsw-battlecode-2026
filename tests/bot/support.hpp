#pragma once

#include "belief.hpp"
#include "state.hpp"

#include <algorithm>
#include <functional>
#include <initializer_list>
#include <iostream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace test {

inline void require(bool condition, std::string const& message) {
    if (!condition)
        throw std::runtime_error(message);
}

// An open, fully observed synthetic board. Tests hide tiles explicitly when exercising partial vision.
inline bot::World world(int width = 16, int height = 16) {
    unswbc::Game game(width, height, 64);
    unswbc::Controller controller(1, unswbc::Team::A, unswbc::Direction::EAST, {}, 64);
    bot::World result;
    result.init(controller, game);
    result.head = result.tileOf(width / 2, height / 2);
    result.length = 4;
    result.unitCount = 4;
    result.round = 100;
    result.stamp = 1;
    result.setAllEdges(bot::EDGE_OPEN);
    std::fill(result.visStamp.begin(), result.visStamp.end(), result.stamp);
    std::fill(result.lastSeen.begin(), result.lastSeen.end(), result.round);
    return result;
}

// The sonar context of a dragon on side `teamB` of test::world()'s default 16 x 16 board.
inline bot::wire::Context wire(bool teamB, int width = 16, int height = 16) { return {teamB, width, height}; }

// Adds an observed head; visible length need not be the dragon's true length.
inline void dragon(bot::World& w, int id, bool enemy, int tile, int visibleLength) {
    w.dragons.push_back({id, enemy, tile, visibleLength});
    w.occId[tile] = id;
    w.occHead[tile] = 1;
    w.occEnemy[tile] = enemy;
    w.touched.push_back(tile);
}

// Sets our tracked body, head first, without relying on protocol parsing.
inline void body(bot::World& w, std::vector<int> const& tiles) {
    w.body.assign(tiles.begin(), tiles.end());
    w.length = static_cast<int>(tiles.size());
    w.head = w.body.front();
    int index = 0;
    for (int tile : tiles) {
        w.occId[tile] = w.myId;
        w.occHead[tile] = index == 0;
        w.occK[tile] = w.length - 1 - index++;
        w.touched.push_back(tile);
    }
    w.dragons.push_back({w.myId, false, w.head, w.length});
}

inline void pearl(bot::World& w, int tile) {
    w.pearlNow[tile] = 1;
    w.pearlSeen[tile] = w.round;
}

// Marks a tile as a known fast tile (seen resetting to 1 twice) that fires next round.
inline void fast(bot::World& w, int tile) {
    w.resetCount[tile] = 2;
    w.resetSum[tile] = 2;
    if (std::find(w.resetTiles.begin(), w.resetTiles.end(), tile) == w.resetTiles.end())
        w.resetTiles.push_back(tile);
    w.spawnKind[tile] = bot::SPAWN_YES;
    w.spawnRound[tile] = w.round + 1;
}

inline void wall(bot::World& w, int tile, int direction) {
    w.setEdge(w.edgeOf(tile, direction), bot::EDGE_KELP);
}

// Runs independent named cases and exits nonzero if any expectation failed.
inline int suite(std::initializer_list<std::pair<char const*, std::function<void()>>> cases) {
    int failures = 0;
    for (auto const& [name, run] : cases) {
        try {
            run();
            std::cout << "PASS " << name << '\n';
        } catch (std::exception const& error) {
            ++failures;
            std::cerr << "FAIL " << name << ": " << error.what() << '\n';
        }
    }
    std::cout << cases.size() << " scenarios, " << failures << " failures\n";
    return failures ? 1 : 0;
}

} // namespace test
