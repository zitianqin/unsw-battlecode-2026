#pragma once

#include "helper.hpp"
#include "params.hpp"

#include <algorithm>
#include <array>
#include <cstdlib>
#include <cstdint>
#include <deque>
#include <map>
#include <vector>

namespace bot {

// Directions are indices 0..3 = N, E, S, W, the same order as a helper Tile's edges.
inline constexpr int DX[4] = {0, 1, 0, -1};
inline constexpr int DY[4] = {-1, 0, 1, 0};
inline constexpr char DIR_CHAR[4] = {'N', 'E', 'S', 'W'};

// step() results that are not a tile.
inline constexpr int KELP = -1;
inline constexpr int UNKNOWN_PORTAL = -2;

// A step count that never arrives: a tile blocked for the whole horizon.
inline constexpr int NEVER = 1 << 29;

enum EdgeKind : std::uint8_t { EDGE_UNKNOWN, EDGE_OPEN, EDGE_KELP, EDGE_PORTAL };
enum SpawnKind : std::uint8_t { SPAWN_UNKNOWN, SPAWN_YES, SPAWN_NEVER };

inline int dirIndex(unswbc::Direction d) {
    switch (d.value) {
    case unswbc::Direction::NORTH: return 0;
    case unswbc::Direction::EAST: return 1;
    case unswbc::Direction::SOUTH: return 2;
    default: return 3;
    }
}

// Another dragon (or ours) seen this turn.
struct SeenDragon {
    int id = -1;
    bool enemy = false;
    int head = -1;       // head tile, or -1 when the head is out of sight
    int visibleLen = 0;  // segments chained to the visible head: a lower bound on its length
};

// Everything one dragon's process knows: static map memory that persists across turns (edges, pearl
// countdowns, when each tile was last seen, our own body) and a snapshot of this turn's vision (who stands
// where and when each body tile frees up). Call observe() once per turn after unswbc::update().
struct World {
    int W = 0, H = 0, N = 0;
    int myId = 0;
    bool teamB = false;
    int round = 0;
    int length = 0;
    int unitCount = 1;
    int unitLimit = 64;
    int head = 0;
    int firstRound = -1;  // the round of this process's first turn (a split child's birth round)
    int bornLength = 0;   // our length on that turn
    int bornHead = -1;    // our head tile on that turn
    int bornUnits = 0;    // our team's unit count on that turn
    int shedRound = -1;   // the round of this process's last trapped-tail shed (shed.hpp), -1 none: main sets it

    // --- Memory across turns ---
    std::vector<std::uint8_t> edgeKind;         // canonical edge -> EdgeKind
    std::vector<int> edgePortal;                // canonical edge -> portal id
    std::map<int, std::array<int, 2>> portalEdges; // portal id -> its (up to) two canonical edges
    std::vector<int> lastSeen;                  // tile -> last round it was in vision, -1 never
    std::vector<int> pearlSeen;                 // tile -> last round a pearl was seen on it, -1 never
    std::vector<int> friendBodySeen;            // tile -> round it last showed a teammate's segment, -1 if it
                                                //   didn't when we last saw it (targets.hpp's crowding)
    std::vector<int> bodySeen;                  // tile -> round it last showed any dragon's segment (ours too), -1 never
    std::vector<int> dropSeen;                  // tile -> round we first saw the drop pearl on it (dropNow), -1 none
    std::vector<int> spawnRound;                // tile -> round of its next known spawn attempt, -1 unknown
    std::vector<int> spawnRead;                 // tile -> round that spawnRound was read (at its mirror, for a
                                                //   mirrored write), -1 unknown
    std::vector<std::uint8_t> spawnKind;        // tile -> SpawnKind
    std::vector<int> resetCount;                // tile -> countdown resets we watched happen (at its mirror, for a
                                                //   mirrored write, symmetry.hpp)
    std::vector<int> resetSum;                  // tile -> sum of the values those resets drew
    std::vector<int> resetTiles;                // every tile watched resetting P.fastMinResets times (itself or its
                                                //   mirror): all fast tiles
    std::vector<int> stepTo;                    // tile * 4 + dir -> computeStep(tile, dir), kept current by setEdge
    int edgeVersion = 0;                        // bumped by every edge memory change (setEdge, setAllEdges): rooms.hpp
                                                //   rebuilds its room map only when it moved
    // stepTo again as whole rows, for bitset searches (Safety::buildOtherHeadDist), on maps at most 64 wide:
    // bit x of openRow[d][y] is set when step((x, y), d) is the plain torus neighbour, and jumpSides lists
    // each side (tile * 4 + dir) whose step lands on some other tile (through a paired portal).
    std::array<std::vector<std::uint64_t>, 4> openRow;
    std::vector<int> jumpSides;
    std::deque<int> body;                       // our body, head first; shorter than length if the tail was never seen

    // --- Map symmetry (symmetry.hpp) ---
    // Candidate k mirrors tile (x, y) onto: 0 (x, H-1-y), 1 (W-1-x, y), 2 (W-1-x, H-1-y), the three a map can use.
    int symLive = 7;                            // bit k set while candidate k fits everything seen
    int symmetry = -1;                          // the inferred or shared candidate, -1 unknown: from then on
                                                //   memory holds mirrored writes
    std::vector<std::uint8_t> edgeMirrored;     // canonical edge -> its memory was written from its mirror, not seen
    std::vector<std::uint8_t> tileMirrored;     // tile -> its countdown memory was written from its mirror, not seen

    // --- This turn ---
    int stamp = 0;
    std::vector<int> visStamp;                  // == stamp when the tile is in vision this turn
    std::vector<int> occId;                     // dragon id standing here, -1 empty
    std::vector<std::uint8_t> occHead;
    std::vector<std::uint8_t> occEnemy;
    std::vector<int> occK;                      // tail distance of the segment here (0 = tail), -1 unknown
    std::vector<std::uint8_t> pearlNow;
    std::vector<int> facing;                    // segment facing (dir index), for chain building
    std::vector<int> behind;                    // the same dragon's segment one step towards its tail, -1 none
    std::vector<int> touched;                   // tiles written this turn (every occupied one), cleared at the next observe()
    std::vector<SeenDragon> dragons;
    std::array<bool, 4> doorProbed{};           // by side of our head: a teammate's doorway probe says its head is
                                                //   where the crossing through that portal edge lands (doorway.hpp)

    void init(unswbc::Controller const& ct, unswbc::Game const& game) {
        W = game.width;
        H = game.height;
        N = W * H;
        myId = ct.get_id();
        teamB = ct.get_team() == unswbc::Team::B;
        unitLimit = game.unit_limit;
        edgeKind.assign(2 * N, EDGE_UNKNOWN);
        edgePortal.assign(2 * N, -1);
        lastSeen.assign(N, -1);
        pearlSeen.assign(N, -1);
        friendBodySeen.assign(N, -1);
        bodySeen.assign(N, -1);
        dropSeen.assign(N, -1);
        spawnRound.assign(N, -1);
        spawnRead.assign(N, -1);
        spawnKind.assign(N, SPAWN_UNKNOWN);
        resetCount.assign(N, 0);
        resetSum.assign(N, 0);
        resetTiles.clear();
        symLive = 7;
        symmetry = -1;
        edgeMirrored.assign(2 * N, 0);
        tileMirrored.assign(N, 0);
        visStamp.assign(N, 0);
        occId.assign(N, -1);
        occHead.assign(N, 0);
        occEnemy.assign(N, 0);
        occK.assign(N, -1);
        pearlNow.assign(N, 0);
        facing.assign(N, 0);
        behind.assign(N, -1);
        stepTo.resize(4 * N);
        for (auto& rows : openRow)
            rows.assign(rowBits() ? H : 0, rowMask());
        jumpSides.clear();
        for (int y = 0; y < H; y++) {
            for (int x = 0; x < W; x++) {
                int* const to = &stepTo[4 * tileOf(x, y)];
                to[0] = tileOf(x, y == 0 ? H - 1 : y - 1);
                to[1] = tileOf(x == W - 1 ? 0 : x + 1, y);
                to[2] = tileOf(x, y == H - 1 ? 0 : y + 1);
                to[3] = tileOf(x == 0 ? W - 1 : x - 1, y);
            }
        }
    }

    bool rowBits() const { return W <= 64; }
    std::uint64_t rowMask() const { return W >= 64 ? ~0ULL : (1ULL << W) - 1; }

    int tileOf(int x, int y) const { return y * W + x; }
    int xOf(int t) const { return t % W; }
    int yOf(int t) const { return t / W; }
    bool visible(int t) const { return visStamp[t] == stamp; }

    // The plain neighbour on the torus, ignoring edges.
    int neighbour(int t, int d) const {
        int const x = (xOf(t) + DX[d] + W) % W;
        int const y = (yOf(t) + DY[d] + H) % H;
        return tileOf(x, y);
    }

    // Wrapped Manhattan distance, ignoring walls.
    int torusDist(int a, int b) const {
        int dx = std::abs(xOf(a) - xOf(b));
        int dy = std::abs(yOf(a) - yOf(b));
        dx = std::min(dx, W - dx);
        dy = std::min(dy, H - dy);
        return dx + dy;
    }

    // Whether b is at most `steps` steps from a through edges and portals we know (unseen edges open, bodies ignored):
    // torusDist that counts kelp. A small search, for radii of a few steps.
    bool pathWithin(int a, int b, int steps) const {
        std::vector<int> frontier{a}, next;
        std::vector<int> seen{a};
        for (int s = 0; s <= steps; s++) {
            for (int t : frontier)
                if (t == b)
                    return true;
            if (s == steps)
                break;
            next.clear();
            for (int t : frontier)
                for (int d = 0; d < 4; d++) {
                    int const to = step(t, d);
                    if (to >= 0 && std::find(seen.begin(), seen.end(), to) == seen.end()) {
                        seen.push_back(to);
                        next.push_back(to);
                    }
                }
            frontier.swap(next);
        }
        return false;
    }

    // Canonical edge id: horizontal edges (a tile's north side) are 0..N-1, vertical (west side) N..2N-1.
    int edgeOf(int t, int d) const {
        int const x = xOf(t), y = yOf(t);
        switch (d) {
        case 0: return tileOf(x, y);
        case 2: return tileOf(x, (y + 1) % H);
        case 3: return N + tileOf(x, y);
        default: return N + tileOf((x + 1) % W, y);
        }
    }

    // The tile you land on after crossing canonical edge e heading d (the engine's TileAfterCrossing).
    int afterCrossing(int e, int d) const {
        if (e < N) {
            int const x = xOf(e), y = yOf(e);
            return d == 2 ? tileOf(x, y) : tileOf(x, (y - 1 + H) % H);
        }
        e -= N;
        int const x = xOf(e), y = yOf(e);
        return d == 1 ? tileOf(x, y) : tileOf((x - 1 + W) % W, y);
    }

    // Tile t under candidate symmetry k (see symLive).
    int mirrorTile(int t, int k) const {
        int const x = k == 0 ? xOf(t) : W - 1 - xOf(t);
        int const y = k == 1 ? yOf(t) : H - 1 - yOf(t);
        return tileOf(x, y);
    }

    // Direction d under candidate symmetry k: a flipped axis swaps its two directions.
    static int mirrorDir(int d, int k) {
        bool const flip = d % 2 == 0 ? k != 1 : k != 0;
        return flip ? (d + 2) % 4 : d;
    }

    // Canonical edge e under candidate symmetry k.
    int mirrorEdge(int e, int k) const {
        return e < N ? edgeOf(mirrorTile(e, k), mirrorDir(0, k)) : edgeOf(mirrorTile(e - N, k), mirrorDir(3, k));
    }

    // The other end of portal edge e, -1 when it is not a portal or its partner is unseen (or e was pushed out
    // of its id's pair by a third edge with that id, which only a test or a wrong symmetry makes).
    int portalPartner(int e) const {
        if (edgeKind[e] != EDGE_PORTAL)
            return -1;
        auto const found = portalEdges.find(edgePortal[e]);
        if (found == portalEdges.end())
            return -1;
        auto const& ends = found->second;
        return ends[0] == e ? ends[1] : ends[1] == e ? ends[0] : -1;
    }

    // Where one step from t heading d lands: a tile, KELP, or UNKNOWN_PORTAL when the partner edge is
    // unseen. Unseen edges are assumed open. A table lookup: edges change only through setEdge.
    int step(int t, int d) const { return stepTo[4 * t + d]; }

    // step() worked out from the edge memory; setEdge caches it in stepTo.
    int computeStep(int t, int d) const {
        int const e = edgeOf(t, d);
        switch (edgeKind[e]) {
        case EDGE_KELP: return KELP;
        case EDGE_PORTAL: {
            int const partner = portalPartner(e);
            return partner < 0 ? UNKNOWN_PORTAL : afterCrossing(partner, d);
        }
        default: return neighbour(t, d);
        }
    }

    // True when every way into t is visible, so a missing segment behind it really is missing.
    bool interior(int t) const {
        for (int d = 0; d < 4; d++) {
            if (edgeKind[edgeOf(t, d)] == EDGE_PORTAL || !visible(neighbour(t, d)))
                return false;
        }
        return true;
    }

    // Recomputes the cached steps across canonical edge e: from its two tiles, out through it.
    void refreshEdgeSteps(int e) {
        int a = 0, da = 0, b = 0, db = 0;
        if (e < N) {
            a = e, da = 0;
            b = tileOf(xOf(e), (yOf(e) - 1 + H) % H), db = 2;
        } else {
            a = e - N, da = 3;
            b = tileOf((xOf(a) - 1 + W) % W, yOf(a)), db = 1;
        }
        cacheStep(a, da);
        cacheStep(b, db);
    }

    // Stores computeStep(t, d) in stepTo, openRow and jumpSides.
    void cacheStep(int t, int d) {
        int const to = computeStep(t, d);
        stepTo[4 * t + d] = to;
        if (!rowBits())
            return;
        int const plain = neighbour(t, d);
        std::uint64_t const bit = 1ULL << xOf(t);
        if (to == plain)
            openRow[d][yOf(t)] |= bit;
        else
            openRow[d][yOf(t)] &= ~bit;
        int const side = 4 * t + d;
        auto const listed = std::find(jumpSides.begin(), jumpSides.end(), side);
        bool const jumps = to >= 0 && to != plain;
        if (jumps && listed == jumpSides.end())
            jumpSides.push_back(side);
        else if (!jumps && listed != jumpSides.end())
            jumpSides.erase(listed);
    }

    // Records what canonical edge e holds (portal id only for EDGE_PORTAL; EDGE_UNKNOWN forgets it) and keeps
    // step() current: a portal's steps change with its own edge and whenever either of its ends becomes known.
    // An edge leaving a portal id (a mirrored write seen or dropped, symmetry.hpp) leaves that id's pair.
    void setEdge(int e, EdgeKind kind, int portalId = -1) {
        if (kind != EDGE_PORTAL)
            portalId = -1;
        int left = -1; // the end of the pair e leaves
        if (edgeKind[e] == EDGE_PORTAL && edgePortal[e] != portalId) {
            if (auto const found = portalEdges.find(edgePortal[e]); found != portalEdges.end()) {
                for (int& end : found->second) {
                    if (end == e)
                        end = -1;
                    else if (end >= 0)
                        left = end;
                }
            }
        }
        if (kind == EDGE_PORTAL) {
            bool changed = edgeKind[e] != EDGE_PORTAL || edgePortal[e] != portalId;
            edgeKind[e] = EDGE_PORTAL;
            edgePortal[e] = portalId;
            auto& ends = portalEdges.try_emplace(portalId, std::array<int, 2>{-1, -1}).first->second;
            std::array<int, 2> const before = ends;
            if (ends[0] != e && ends[1] != e) {
                (ends[0] < 0 ? ends[0] : ends[1]) = e;
                changed = true;
            }
            if (changed) {
                edgeVersion++;
                refreshEdgeSteps(e);
                for (int end : {before[0], before[1], ends[0], ends[1], left})
                    if (end >= 0)
                        refreshEdgeSteps(end);
            }
        } else if (edgeKind[e] != kind) {
            edgeKind[e] = kind;
            edgePortal[e] = -1;
            edgeVersion++;
            refreshEdgeSteps(e);
            if (left >= 0)
                refreshEdgeSteps(left);
        }
    }

    // setEdge for every edge, then every step recomputed: for tests building a map by hand.
    void setAllEdges(EdgeKind kind) {
        std::fill(edgeKind.begin(), edgeKind.end(), kind);
        edgeVersion++;
        for (int t = 0; t < N; t++)
            for (int d = 0; d < 4; d++)
                cacheStep(t, d);
    }

    void rememberEdge(int e, unswbc::Edge const& edge) {
        if (edge.is_portal())
            setEdge(e, EDGE_PORTAL, edge.get_portal_id());
        else
            setEdge(e, edge.is_passable() ? EDGE_OPEN : EDGE_KELP);
    }

    // Reads this turn's vision into memory and rebuilds the snapshot.
    void observe(unswbc::Controller const& ct, unswbc::Game const& game) {
        round = game.round_num;
        length = ct.get_length();
        unitCount = ct.get_unit_count();
        if (firstRound < 0) {
            firstRound = round;
            bornLength = length;
            bornUnits = unitCount;
        }
        stamp++;
        for (int t : touched) {
            occId[t] = -1;
            occHead[t] = 0;
            occEnemy[t] = 0;
            occK[t] = -1;
            pearlNow[t] = 0;
            behind[t] = -1;
        }
        touched.clear();
        dragons.clear();

        auto const myTeam = ct.get_team();
        for (auto const& tile : ct.get_tiles()) {
            int const t = tileOf(tile.position.x, tile.position.y);
            visStamp[t] = stamp;
            int const seenBefore = lastSeen[t];
            bool const pearlBefore = seenBefore >= 0 && pearlSeen[t] == seenBefore;
            lastSeen[t] = round;
            touched.push_back(t);
            if (tile.has_pearl()) {
                pearlNow[t] = 1;
                pearlSeen[t] = round;
                // A drop (dropNow; P.harvestDrops, P.harvestStep): a pearl on a tile that held a dragon's segment when we last saw it, and
                // no pearl then. A spawn needs an empty tile, so it can only follow a body that moved off.
                bool const fired = spawnKind[t] == SPAWN_YES && spawnRound[t] >= seenBefore && spawnRound[t] <= round;
                if (!pearlBefore && seenBefore >= 0 && bodySeen[t] == seenBefore && !fired)
                    dropSeen[t] = round;
                else if (!pearlBefore)
                    dropSeen[t] = -1;
            } else {
                dropSeen[t] = -1;
            }
            if (tile.get_dragon())
                bodySeen[t] = round;
            noteCountdown(t, tile.get_pearl_time());
            friendBodySeen[t] = -1;
            for (int d = 0; d < 4; d++)
                rememberEdge(edgeOf(t, d), tile.edges[d]);
            if (auto const* part = tile.get_dragon()) {
                occId[t] = part->get_id();
                occHead[t] = part->is_head() ? 1 : 0;
                occEnemy[t] = part->get_team() == myTeam ? 0 : 1;
                facing[t] = dirIndex(part->get_dir());
                if (!occEnemy[t] && occId[t] != myId)
                    friendBodySeen[t] = round;
            }
        }
        head = tileOf(ct.get_position().x, ct.get_position().y);
        if (round == firstRound)
            bornHead = head;

        linkSegments();
        trackOwnBody();
        assignTailDistances();
    }

    // Records a visible tile's countdown. When the countdown we saw earlier ran out at this round's tick,
    // the value now is a fresh draw from the tile's hidden [min_gap, max_gap]: those draws tell fast tiles.
    void noteCountdown(int t, int countdown) {
        if (countdown < 0) {
            spawnKind[t] = SPAWN_NEVER;
            return;
        }
        if (spawnKind[t] == SPAWN_YES && spawnRound[t] == round && resetCount[t] < P.fastMaxResets) {
            resetCount[t]++;
            resetSum[t] += countdown;
            if (resetCount[t] == P.fastMinResets)
                resetTiles.push_back(t);
        }
        spawnKind[t] = SPAWN_YES;
        spawnRound[t] = round + countdown;
        spawnRead[t] = round;
    }

    // True for a tile we have watched reset often enough, to a small enough mean gap, to count on it
    // holding a pearl whenever it is empty. Only tiles in resetTiles can be fast.
    static_assert(P.fastMinResets >= 1, "resetTiles lists tiles as they reach P.fastMinResets resets");
    bool fastTile(int t) const {
        return resetCount[t] >= P.fastMinResets && resetSum[t] <= P.fastMaxGap * resetCount[t];
    }

    // For every visible body segment, record which segment sits one step behind the one it points at.
    void linkSegments() {
        for (int t : touched) {
            if (occId[t] < 0 || occHead[t])
                continue;
            int const ahead = step(t, facing[t]);
            if (ahead >= 0 && occId[ahead] == occId[t] && visible(ahead))
                behind[ahead] = t;
        }
        for (int t : touched) {
            if (occId[t] < 0 || !occHead[t])
                continue;
            SeenDragon seen;
            seen.id = occId[t];
            seen.enemy = occEnemy[t];
            seen.head = t;
            for (int cur = t; cur >= 0 && seen.visibleLen <= 49; cur = behind[cur])
                seen.visibleLen++;
            dragons.push_back(seen);
        }
    }

    // Keeps our body in memory: each turn the new head goes on the front and the list is trimmed to our
    // length (eating, splitting and portals all fall out of that). Rebuilt from vision if they disagree.
    void trackOwnBody() {
        if (body.empty() || body.front() != head)
            body.push_front(head);
        while (static_cast<int>(body.size()) > length)
            body.pop_back();

        std::vector<int> chain;
        for (int cur = head; cur >= 0 && static_cast<int>(chain.size()) < length; cur = behind[cur])
            chain.push_back(cur);
        bool agrees = chain.size() <= body.size();
        for (std::size_t i = 0; agrees && i < chain.size(); i++)
            agrees = body[i] == chain[i];
        for (std::size_t i = chain.size(); agrees && i < body.size(); i++)
            agrees = !visible(body[i]) || occId[body[i]] == myId;
        if (!agrees)
            body.assign(chain.begin(), chain.end());
    }

    // Tail distance k for every segment we can pin down: our own from the tracked body, others' from a
    // visible tail. A tile with k frees up k+2 of our steps from now (the tail is still there on step 1).
    // A fresh split child tracks only the chain it follows from its head in view; with P.numberOwnTail its
    // other own segments are numbered from our own visible tail like another dragon's.
    void assignTailDistances() {
        for (std::size_t i = 0; i < body.size(); i++) {
            int const t = body[i];
            if (!visible(t)) {
                occId[t] = myId;
                occHead[t] = i == 0;
                occEnemy[t] = 0;
                touched.push_back(t);
            }
            occK[t] = length - 1 - static_cast<int>(i);
        }
        for (int t : touched) {
            if (occId[t] < 0 || occK[t] >= 0 || behind[t] >= 0 || !interior(t))
                continue;
            if (occId[t] == myId && !P.numberOwnTail)
                continue;
            // t is a confirmed tail we have not tracked: walk towards the head numbering segments.
            int k = 0;
            for (int cur = t; cur >= 0 && occId[cur] == occId[t] && k <= 49; k++) {
                occK[cur] = k;
                if (occHead[cur])
                    break;
                int const ahead = step(cur, facing[cur]);
                cur = ahead >= 0 && visible(ahead) ? ahead : -1;
            }
        }
    }

    // First step at which tile t can be entered (1 = this turn). selfDelay adds to our own segments,
    // e.g. 1 when this move eats a pearl and our tail stays put. Our own segment with unknown tail distance
    // (off the tracked body) is blocked until the latest step any untracked segment could free.
    int freeAt(int t, int selfDelay) const {
        if (occId[t] < 0)
            return 0;
        if (occId[t] == myId) {
            if (occK[t] < 0 && P.blockUntrackedOwn)
                return std::max(2, length - static_cast<int>(body.size()) + 1) + selfDelay;
            return occK[t] + 2 + selfDelay;
        }
        if (occHead[t] || occK[t] < 0)
            return NEVER;
        return occK[t] + 2 + P.otherTailMargin;
    }

    bool hasPearlHere(int t) const { return visible(t) ? pearlNow[t] != 0 : false; }

    // True for a visible pearl a dragon's death dropped (dropSeen).
    bool dropNow(int t) const { return visible(t) && pearlNow[t] && dropSeen[t] >= 0; }

    SeenDragon const* dragonAt(int t) const {
        for (auto const& seen : dragons)
            if (seen.head == t)
                return &seen;
        return nullptr;
    }
};

// Path lengths from one source tile (or from every enemy head) over what we know of the map. Tiles that stay blocked all horizon
// (heads, bodies whose tail we cannot see) get a distance but are not expanded through.
struct DistField {
    std::vector<int> dist;
    std::vector<int> mark;
    std::vector<int> queue;
    int stamp = 0;
    int source = -1;

    static constexpr int FAR = 100000;

    int at(int t) const { return source >= 0 && mark[t] == stamp ? dist[t] : FAR; }

    // BFS from src out to maxDepth; stops early one layer past `stopTile` once it is reached. With
    // `emptyOnly`, only visible empty tiles are expanded, as a sprint ram walks (hunt.hpp).
    void build(World const& w, int src, int maxDepth, int stopTile = -1, bool emptyOnly = false) {
        begin(w);
        source = src;
        queue[0] = src;
        mark[src] = stamp;
        dist[src] = 0;
        grow(w, 1, maxDepth, stopTile, emptyOnly);
    }

    // BFS from every visible enemy head at once out to maxDepth: steps for the nearest one to reach each tile
    // (FAR when none can). Used as a fill's rival to count only tiles we reach before every enemy head.
    void buildEnemyHeads(World const& w, int maxDepth) {
        begin(w);
        source = -1;
        std::size_t tail = 0;
        for (auto const& d : w.dragons) {
            if (!d.enemy || mark[d.head] == stamp)
                continue;
            source = d.head;
            queue[tail++] = d.head;
            mark[d.head] = stamp;
            dist[d.head] = 0;
        }
        grow(w, tail, maxDepth, -1, false);
    }

private:
    void begin(World const& w) {
        if (dist.empty()) {
            dist.assign(w.N, 0);
            mark.assign(w.N, 0);
            queue.assign(w.N, 0); // scratch: every tile enters at most once
        }
        stamp++;
    }

    // Expands from the `tail` tiles seeded in the queue; sources are expanded even when blocked (a head).
    void grow(World const& w, std::size_t tail, int maxDepth, int stopTile, bool emptyOnly) {
        int const st = stamp;
        int* const q = queue.data();
        int* const d = dist.data();
        int* const m = mark.data();
        int const* const steps = w.stepTo.data();
        std::size_t const seeds = tail;
        int stopDepth = maxDepth;
        for (std::size_t at = 0; at < tail; at++) {
            int const cur = q[at];
            int const here = d[cur];
            if (cur == stopTile)
                stopDepth = std::min(stopDepth, here + 1);
            if (here >= stopDepth)
                continue;
            if (at >= seeds && (emptyOnly ? w.occId[cur] >= 0 || !w.visible(cur) : w.freeAt(cur, 0) >= NEVER))
                continue;
            int const* const out = steps + 4 * cur;
            for (int dir = 0; dir < 4; dir++) {
                int const to = out[dir];
                if (to < 0 || m[to] == st)
                    continue;
                m[to] = st;
                d[to] = here + 1;
                q[tail++] = to;
            }
        }
    }
};


// The final fold's first round with P.modeModel: fold.hpp's startFold sets it each turn from the map's size.
inline int modelFoldStart = unswbc::Constants::MAX_ROUNDS - static_cast<int>(P.foldWindow);

// The first round of the final fold: with P.modeModel modelFoldStart, else P.foldRound, but no earlier than
// P.beaconRound, since a seeding king (fold.hpp's kingSeedOn) reports itself before the beacon and the fold waits for
// the beacon as it does on every other map.
inline int foldStart() { return P.modeModel ? modelFoldStart : std::max(P.foldRound, P.beaconRound); }

// The first round of the king beacon (fold.hpp's isKing): P.beaconRound, or the fold's start when P.modeModel starts
// it earlier, since folders walk only to a reported king.
inline int beaconStart() { return P.modeModel ? std::min(P.beaconRound, foldStart()) : P.beaconRound; }

// True when dragon `id` of length `len` merges into teammate `mateId` of length `mateLen` (feed.hpp's merge
// cascade, the final fold): the teammate is at least as long, and on a tie the lower id survives. Lengths seen from
// outside are visible chains, a lower bound, so a wrong read only keeps a dragon alive.
inline bool mergesInto(int len, int id, int mateLen, int mateId) {
    return mateLen > len || (mateLen == len && mateId < id);
}

// The first round of the endgame for sheds under threat (split.hpp's shedLength): P.beaconRound, P.foldRound or
// P.mergeRound, whichever comes first. From it a long dragon sheds only as split.hpp's threatShedOn allows (the king,
// or with P.longShedsInFold every dragon, when this round decides nothing); before it every long dragon sheds, a king seeding before the beacon (fold.hpp's kingSeedOn) and
// one in a fold P.modeModel starts earlier (E0345's fit played it so) included.
inline int threatShedEnd() { return std::min({P.beaconRound, P.foldRound, P.mergeRound}); }

// The unit slots ordinary splits leave free this round for rescue splits (split-outs, sheds, sheds under threat):
// P.rescueSlots on a map of at least P.reserveMinArea tiles from P.rescueFrom until P.mergeRound with
// P.reserveRescueSlots, else 0; and on any map at least P.shedReserve while sheds under threat are on
// (P.shedUnderThreat, before threatShedEnd, or at any round with P.longShedsInFold; a king shedding under
// P.kingShedsInFold alone gets none). shouldSplit (split.hpp) stops at unitLimit - rescueReserve, so that is
// where our swarm saturates; farmEntryOk (farm.hpp) and Targets::crowdActive read it too.
inline int rescueReserve(World const& w) {
    bool const on = P.reserveRescueSlots && w.N >= P.reserveMinArea && w.round >= P.rescueFrom &&
                    w.round < P.mergeRound;
    int const shed = P.shedUnderThreat && (w.round < threatShedEnd() || P.longShedsInFold) ? P.shedReserve : 0;
    return std::max(on ? P.rescueSlots : 0, shed);
}

} // namespace bot
