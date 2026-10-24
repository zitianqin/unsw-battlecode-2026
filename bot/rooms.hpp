#pragma once

#include "state.hpp"

#include <algorithm>
#include <cstdint>
#include <utility>
#include <vector>

namespace bot {

// The room map: our known map (World's edges, mirrored writes and teammates' shared kelp included) cut into rooms, each with its exits, so a
// dragon can name the room it is in, how it gets out, and where each portal out of it lands. The debug line
// reads it (main, with kNotes), and so do the room exit (roomexit.hpp, with P.portalRoomExit) and the jammed room's
// (jamroom.hpp, with P.leaveJam).
//
// A room is a maximal region joined by passages at least 3 wide: the tiles covered by open 3x3 blocks (every edge
// inside the block open or unseen; unseen edges are assumed open, as everywhere in the bot, and kelp and portal
// edges close a block), two blocks one step apart side by side or up and down in the same room. Tiles no open block
// covers (corridors, nooks and stubs 1-2 wide) form thin rooms of their own, joined through open edges. Bodies are
// not walls: they move.
//
// A room's exits are its doors, the other rooms it touches through an open edge (passages 1-2 wide), that lead
// somewhere, and its portal edges: known when we know the pair, so step() says where the crossing lands and
// portalExits records the landing tile's room, else unknown. A thin room with one door (a nook, a stub or an alcove,
// whatever it holds) is a dead end, no way out: it folds into the room it opens onto and brings its portal edges with
// it (an alcove with a portal at its back is that portal, reached from the room). It keeps its own id and tiles, and
// its one door is that room. Wide rooms never fold, however they end: a pocket 3 wide is a room with its own doors.
// A room's area is the room it folds into (itself when it doesn't), and portalOnly asks whether that area is a room
// that only portals leave, so a nook of such a room is in it too.
//
// Call update() whenever the map is wanted: it rebuilds only when World::edgeVersion moved since its last build.
struct RoomMap {
    struct Info {
        int tiles = 0;
        int doors = 0;          // other rooms it touches through an open edge, less the dead ends folded into it
        int knownPortals = 0;   // portal edges on its tiles or its folded dead ends' whose pair we know
        int unknownPortals = 0; // ... and whose pair we don't
        int area = 0;           // the room it folds into, itself when it doesn't
        int areaTiles = 0;      // that area's tiles: the area room's and its folded dead ends'
        bool wide = false;      // a room of open 3x3 blocks, not a thin one (a corridor, nook or stub)
    };
    // One way through a portal edge: `side` is tile * 4 + dir, stepping from that tile heading dir; `room` is that
    // tile's room and `landing` the room it lands in, -1 while the pair is unknown.
    struct PortalExit {
        int side = 0;
        int room = 0;
        int landing = -1;
    };

    std::vector<int> roomOf; // tile -> room id, ids from 0 in the order of each room's lowest tile
    std::vector<Info> rooms;
    std::vector<PortalExit> portalExits; // ascending side
    int builtVersion = -1;
    int mapTiles = 0;

    // True when `room`'s area is a room that only portals leave: no door, at least one portal edge, and less than
    // half the map's tiles, so most of the map lies through a portal. A dragon's whole walkable ground, when no door
    // leaves it, is no such room: it is where the rest of the map's portals lead.
    bool portalOnly(int room) const {
        Info const& a = rooms[rooms[room].area];
        return a.doors == 0 && a.knownPortals + a.unknownPortals > 0 && 2 * a.areaTiles < mapTiles;
    }

    void update(World const& w) {
        if (builtVersion == w.edgeVersion && static_cast<int>(roomOf.size()) == w.N)
            return;
        builtVersion = w.edgeVersion;
        build(w);
    }

private:
    std::vector<int> region, queue, children, foldInto, adjStart, adj, compact, counted;
    std::vector<std::pair<int, int>> pairs;

    static bool passable(World const& w, int t, int d) {
        std::uint8_t const kind = w.edgeKind[w.edgeOf(t, d)];
        return kind == EDGE_OPEN || kind == EDGE_UNKNOWN;
    }

    // The two tiles beside canonical edge e and the direction from each across it.
    struct Sides {
        int a, da, b, db;
    };
    static Sides sides(World const& w, int e) {
        if (e < w.N)
            return {e, 0, w.neighbour(e, 0), 2};
        return {e - w.N, 3, w.neighbour(e - w.N, 3), 1};
    }

    // Labels every tile's region: rooms of open blocks first, then the uncovered tiles' components. Returns the
    // number of regions; those from `wide` on are thin.
    int label(World const& w, int& wide) {
        int const N = w.N;
        region.assign(N, -1);
        wide = 0;
        if (w.W >= 3 && w.H >= 3) {
            std::vector<std::uint8_t> hRun(N), vRun(N), block(N);
            std::vector<int> blockRoom(N, -1);
            for (int t = 0; t < N; t++) {
                hRun[t] = passable(w, t, 1) && passable(w, w.neighbour(t, 1), 1);
                vRun[t] = passable(w, t, 2) && passable(w, w.neighbour(t, 2), 2);
            }
            for (int a = 0; a < N; a++) {
                int const s1 = w.neighbour(a, 2), s2 = w.neighbour(s1, 2);
                int const e1 = w.neighbour(a, 1), e2 = w.neighbour(e1, 1);
                block[a] = hRun[a] && hRun[s1] && hRun[s2] && vRun[a] && vRun[e1] && vRun[e2];
            }
            for (int a = 0; a < N; a++) {
                if (!block[a] || blockRoom[a] >= 0)
                    continue;
                blockRoom[a] = wide;
                queue.assign(1, a);
                for (std::size_t at = 0; at < queue.size(); at++)
                    for (int d = 0; d < 4; d++)
                        if (int const b = w.neighbour(queue[at], d); block[b] && blockRoom[b] < 0) {
                            blockRoom[b] = wide;
                            queue.push_back(b);
                        }
                wide++;
            }
            // A tile's room is the lowest of the rooms whose blocks cover it (blocks anchored up to 2 west and north).
            for (int a = 0; a < N; a++) {
                if (!block[a])
                    continue;
                for (int row = 0, r = a; row < 3; row++, r = w.neighbour(r, 2))
                    for (int col = 0, t = r; col < 3; col++, t = w.neighbour(t, 1))
                        if (region[t] < 0 || blockRoom[a] < region[t])
                            region[t] = blockRoom[a];
            }
        }
        int count = wide;
        std::vector<std::uint8_t> covered(N);
        for (int t = 0; t < N; t++)
            covered[t] = region[t] >= 0;
        for (int t = 0; t < N; t++) {
            if (region[t] >= 0)
                continue;
            region[t] = count;
            queue.assign(1, t);
            for (std::size_t at = 0; at < queue.size(); at++) {
                int const cur = queue[at];
                for (int d = 0; d < 4; d++)
                    if (int const to = w.neighbour(cur, d); !covered[to] && region[to] < 0 && passable(w, cur, d)) {
                        region[to] = count;
                        queue.push_back(to);
                    }
            }
            count++;
        }
        return count;
    }

    void build(World const& w) {
        int const N = w.N;
        int wide = 0;
        int const count = label(w, wide);

        // Doors between regions: each pair of regions touching through an open edge, once.
        pairs.clear();
        for (int t = 0; t < N; t++)
            for (int d : {1, 2})
                if (int const u = region[t], v = region[w.neighbour(t, d)]; u != v && passable(w, t, d)) {
                    pairs.emplace_back(u, v);
                    pairs.emplace_back(v, u);
                }
        std::sort(pairs.begin(), pairs.end());
        pairs.erase(std::unique(pairs.begin(), pairs.end()), pairs.end());
        adjStart.assign(count + 1, 0);
        for (auto const& [u, v] : pairs)
            adjStart[u + 1]++;
        for (int r = 0; r < count; r++)
            adjStart[r + 1] += adjStart[r];
        adj.resize(pairs.size());
        for (std::size_t i = 0; i < pairs.size(); i++)
            adj[i] = pairs[i].second;

        // Fold dead ends: a thin region's neighbours are all wide (thin tiles that touch share a region), so a thin
        // region with one door folds into a wide room, which never folds.
        children.assign(count, 0);
        foldInto.assign(count, -1);
        for (int r = wide; r < count; r++) {
            if (adjStart[r + 1] - adjStart[r] != 1)
                continue;
            foldInto[r] = adj[adjStart[r]];
            children[foldInto[r]]++;
        }

        // Compact ids in the order of each room's lowest tile.
        compact.assign(count, -1);
        rooms.clear();
        roomOf.assign(N, -1);
        for (int t = 0; t < N; t++) {
            int const r = region[t];
            if (compact[r] < 0) {
                compact[r] = static_cast<int>(rooms.size());
                rooms.push_back({.doors = adjStart[r + 1] - adjStart[r] - children[r], .wide = r < wide});
            }
            rooms[compact[r]].tiles++;
            roomOf[t] = compact[r];
        }
        // A wide region can end with no tiles (lower rooms' blocks took them all): it has no id and no room.
        for (int r = 0; r < count; r++) {
            if (compact[r] < 0)
                continue;
            Info& room = rooms[compact[r]];
            room.area = compact[foldInto[r] >= 0 ? foldInto[r] : r];
            rooms[room.area].areaTiles += room.tiles;
        }
        for (Info& room : rooms)
            room.areaTiles = rooms[room.area].areaTiles;
        mapTiles = N;

        // Each portal edge counts once for every room it is an exit of: the rooms beside it and those they fold into.
        counted.assign(count, -1);
        portalExits.clear();
        for (int e = 0; e < 2 * N; e++) {
            if (w.edgeKind[e] != EDGE_PORTAL)
                continue;
            bool const known = w.portalPartner(e) >= 0;
            auto const [a, da, b, db] = sides(w, e);
            for (int start : {region[a], region[b]})
                for (int r = start; r >= 0 && counted[r] != e; r = foldInto[r]) {
                    counted[r] = e;
                    (known ? rooms[compact[r]].knownPortals : rooms[compact[r]].unknownPortals)++;
                }
            for (auto [t, d] : {std::pair{a, da}, std::pair{b, db}}) {
                int const to = w.step(t, d);
                portalExits.push_back({4 * t + d, roomOf[t], to >= 0 ? roomOf[to] : -1});
            }
        }
        std::sort(portalExits.begin(), portalExits.end(),
                  [](PortalExit const& x, PortalExit const& y) { return x.side < y.side; });
    }
};

} // namespace bot
