#include "support.hpp"
#include "doorway.hpp"
#include "gossip.hpp"
#include "rooms.hpp"
#include "scoring.hpp"

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <functional>
#include <utility>

using test::require;

namespace {

// A fully seen 5x3 room (x 2..6, y 2..4) walled with kelp, except that the east edge of (6,3) is a portal
// whose partner we have never seen. `openWest` instead leaves a gap in the west wall at (2,3) onto
// never-seen ground. `map` is the room map a Targets reads when a test sets its `rooms` to it (roomexit.hpp). The
// default constructor has us playing since round 0, one of test::world()'s unitCount dragons a team then; the
// width x height one leaves firstRound unset, as for a split child.
// The openWest room also walls in a never-seen tile at (10,8) that no known path reaches, so its portal may still
// lead somewhere new (P.portalFarFood).
struct Room {
    bot::World w = test::world();
    bot::RoomMap map;
    int portalTile = 0;

    // A room of the tiles `inside` picks on a width x height board, walled with kelp all round except for the
    // given (tile, direction) edges, which are portals whose partners we have never seen.
    Room(int width, int height, std::function<bool(int, int)> const& inside,
         std::vector<std::pair<int, int>> const& portals)
        : w(test::world(width, height)) {
        for (int t = 0; t < w.N; t++) {
            if (!inside(w.xOf(t), w.yOf(t)))
                continue;
            for (int dir = 0; dir < 4; dir++) {
                int const to = w.neighbour(t, dir);
                if (!inside(w.xOf(to), w.yOf(to)))
                    test::wall(w, t, dir);
            }
        }
        for (auto const& [tile, dir] : portals)
            w.rememberEdge(w.edgeOf(tile, dir), unswbc::Edge(false, unswbc::EdgeType::PORTAL, 5 + tile));
        portalTile = portals.front().first;
    }

    // This dragon has seen only the 7x7 around `at` (a split child just born there); the rest of the board is
    // out of sight and was never seen by it.
    void seeOnlyAround(int at) {
        auto const near = [](int a, int b, int size) { return std::min(std::abs(a - b), size - std::abs(a - b)) <= 3; };
        for (int t = 0; t < w.N; t++) {
            if (near(w.xOf(t), w.xOf(at), w.W) && near(w.yOf(t), w.yOf(at), w.H))
                continue;
            w.visStamp[t] = 0;
            w.lastSeen[t] = -1;
        }
    }

    // The team's belief with every tile `inside` picks reported by a teammate this round (and our own view).
    bot::Belief teamSaw(std::function<bool(int, int)> const& inside) const {
        bot::Belief b;
        b.init(w);
        b.observe(w);
        for (int t = 0; t < w.N; t++)
            if (inside(w.xOf(t), w.yOf(t)))
                b.see(t, w.round, false);
        return b;
    }

    explicit Room(bool openWest = false) {
        w.firstRound = 0;
        w.bornUnits = w.unitCount;
        auto const inside = [](int x, int y) { return x >= 2 && x <= 6 && y >= 2 && y <= 4; };
        for (int y = 2; y <= 4; y++) {
            for (int x = 2; x <= 6; x++) {
                int const t = w.tileOf(x, y);
                for (int dir = 0; dir < 4; dir++)
                    if (!inside(x + bot::DX[dir], y + bot::DY[dir]))
                        test::wall(w, t, dir);
            }
        }
        portalTile = w.tileOf(6, 3);
        w.rememberEdge(w.edgeOf(portalTile, 1), unswbc::Edge(false, unswbc::EdgeType::PORTAL, 5));
        if (openWest) {
            w.setEdge(w.edgeOf(w.tileOf(2, 3), 3), bot::EDGE_OPEN);
            for (int dir = 0; dir < 4; dir++)
                test::wall(w, w.tileOf(10, 8), dir);
            for (int y = 0; y < w.H; y++) {
                for (int x = 0; x < 2; x++) {
                    w.visStamp[w.tileOf(x, y)] = 0;
                    w.lastSeen[w.tileOf(x, y)] = -1;
                }
            }
        }
    }

    // Places our dragon, head first, as the only one on the board.
    void place(std::vector<int> const& tiles) {
        for (int t : w.body) {
            w.occId[t] = -1;
            w.occHead[t] = 0;
            w.occK[t] = -1;
        }
        w.dragons.clear();
        test::body(w, tiles);
    }

    // Plays one turn of targeting and move choice and applies it. Returns the move; a move whose dest is
    // UNKNOWN_PORTAL leaves the board unchanged (the caller decides where it landed).
    bot::MoveEval turn(bot::Targets& targets) {
        bot::Safety safety;
        safety.prepare(w);
        map.update(w);
        targets.update(w);
        auto const move = bot::bestMove(w, safety, targets);
        if (move.dest >= 0) {
            bool const eats = w.pearlNow[move.dest] != 0;
            w.pearlNow[move.dest] = 0;
            std::vector<int> next{move.dest};
            next.insert(next.end(), w.body.begin(), w.body.end());
            if (!eats)
                next.pop_back();
            place(next);
        }
        w.round++;
        return move;
    }
};

// Plays up to `turns` turns and returns the number played before stepping into the unknown portal, or -1.
int turnsToCross(Room& room, bot::Targets& targets, int turns, std::vector<int>* heads = nullptr) {
    for (int i = 0; i < turns; i++) {
        auto const move = room.turn(targets);
        if (move.dest == bot::UNKNOWN_PORTAL)
            return i;
        require(move.score > P.doomedScore, "every move before the crossing must be safe");
        if (heads)
            heads->push_back(room.w.head);
    }
    return -1;
}

void closedRoomWalksToPortalAndCrosses() {
    Room room;
    room.place({room.w.tileOf(2, 2), room.w.tileOf(2, 3), room.w.tileOf(2, 4)});
    bot::Targets targets;
    int const crossedAfter = turnsToCross(room, targets, 12);
    require(crossedAfter >= 0 && crossedAfter <= 8,
            "a length-3 dragon in a seen, pearl-free room must walk to its only exit, an unknown portal, and cross");
    require(room.w.head == room.portalTile, "the crossing starts from the tile beside the portal");
}

// With the room exit, a pearl is eaten first only while it is eaten sooner than the dragon could cross: from (4,3)
// the crossing is 3 moves (two steps to the portal tile, then through).
void pearlSoonerThanTheCrossingIsEatenFirst() {
    for (int pearlX : {3, 2}) {
        Room room;
        room.place({room.w.tileOf(4, 3), room.w.tileOf(3, 3), room.w.tileOf(2, 3)});
        int const pearl = room.w.tileOf(pearlX, 2); // 2 or 3 moves away, on the side away from the portal
        test::pearl(room.w, pearl);
        bot::Targets targets;
        targets.rooms = &room.map;
        std::vector<int> heads;
        int const crossedAfter = turnsToCross(room, targets, 16, &heads);
        auto const ate = std::find(heads.begin(), heads.end(), pearl);
        if (pearlX == 3) {
            require(ate != heads.end() && ate - heads.begin() <= 2, "a pearl 2 moves away is eaten first");
            require(crossedAfter > ate - heads.begin(), "only then does the dragon leave through the portal");
        } else {
            require(crossedAfter >= 0 && crossedAfter <= 2 && ate == heads.end(),
                    "a pearl no sooner than the crossing waits: the dragon crosses first");
        }
    }
}

// A room only portals leave, with ground in it we have never seen (the west column): the room exit sends us through
// the portal now, unless a teammate in view is nearer it.
void portalOnlyRoomIsLeftBeforeItsUnseenGround() {
    auto const room = [] {
        Room r;
        for (int y = 2; y <= 4; y++) {
            r.w.lastSeen[r.w.tileOf(2, y)] = -1;
            r.w.visStamp[r.w.tileOf(2, y)] = 0;
        }
        r.place({r.w.tileOf(6, 4), r.w.tileOf(5, 4), r.w.tileOf(4, 4)});
        return r;
    };
    {
        Room r = room();
        bot::Targets targets;
        targets.rooms = &r.map;
        r.map.update(r.w);
        require(r.map.portalOnly(r.map.roomOf[r.w.head]), "the walled room is portal-only on the room map");
        int const crossedAfter = turnsToCross(r, targets, 4);
        require(crossedAfter == 1 && r.w.head == r.portalTile, "the room exit walks to the portal and crosses");
    }
    {
        Room r = room();
        test::dragon(r.w, 2, false, r.w.tileOf(6, 2), 3); // one step from the portal tile, we are one step too
        test::dragon(r.w, 0, false, r.w.tileOf(5, 3), 3); // and the lower id, also one step from it
        bot::Targets targets;
        targets.rooms = &r.map;
        r.map.update(r.w);
        targets.update(r.w);
        require(!targets.exploreFromRoom && targets.explorePortalDir < 0,
                "a teammate as near the portal with the lower id takes it; we explore as before");
    }
    {
        Room r = room();
        r.w.firstRound = r.w.round - 1; // a split child, born in the room last round
        bot::Targets targets;
        targets.rooms = &r.map;
        r.map.update(r.w);
        targets.update(r.w);
        require(!targets.exploreFromRoom, "with P.portalRoomExitStarters a split child doesn't take the room exit");
    }
    {
        Room r = room();
        r.place({r.w.tileOf(4, 4), r.w.tileOf(3, 4), r.w.tileOf(2, 4)}); // 3 steps from the portal tile
        test::dragon(r.w, 2 * r.w.bornUnits, false, r.w.tileOf(6, 2), 3); // the first child's id, 1 step from it
        bot::Targets targets;
        targets.rooms = &r.map;
        r.map.update(r.w);
        targets.update(r.w);
        require(targets.exploreFromRoom && targets.explore == r.portalTile,
                "a nearer split child doesn't take the exit, so the round-0 dragon doesn't yield it");
    }
}

// P.portalFarFood: the same room, but every tile outside it was seen and never spawns (Autarky's barren portal boxes
// seen from outside). On, the portal can lead nowhere with food, so it is no target; off, it is still frontier and the
// dragon crosses. closedRoomWalksToPortalAndCrosses is the case with ground outside still unseen.
void portalIntoSeenBarrenGround() {
    Room room;
    for (int t = 0; t < room.w.N; t++) {
        int const x = room.w.xOf(t), y = room.w.yOf(t);
        if (!(x >= 2 && x <= 6 && y >= 2 && y <= 4))
            room.w.spawnKind[t] = bot::SPAWN_NEVER;
    }
    room.place({room.w.tileOf(4, 3), room.w.tileOf(3, 3), room.w.tileOf(2, 3)});
    bot::Targets targets;
    targets.update(room.w);
    if (P.portalFarFood) {
        require(targets.explorePortalDir < 0, "a portal whose far side can hold no food is not an explore target");
        require(turnsToCross(room, targets, 8) < 0, "the dragon stays in the room");
    } else {
        require(targets.explorePortalDir >= 0, "without the far-food gate the portal stays an explore target");
        require(turnsToCross(room, targets, 8) >= 0, "the dragon leaves the room through the portal");
    }
}

// With P.portalFrontier the unknown portal is explore frontier beside the ground, priced as never-seen ground one
// step past its tile (less its landing risk with P.portalLandingRisk): never-seen ground nearer than that wins ...
void nearerNeverSeenGroundBeatsThePortal() {
    Room room(true);
    for (int y = 0; y < room.w.H; y++) {
        for (int x = 14; x < room.w.W; x++) {
            room.w.visStamp[room.w.tileOf(x, y)] = 0;
            room.w.lastSeen[room.w.tileOf(x, y)] = -1;
        }
    }
    // Explore grid tile (14,4) is 4 steps away by torus distance (explore targets are ranked by it); the portal 5 and
    // one through.
    room.place({room.w.tileOf(2, 4), room.w.tileOf(3, 4), room.w.tileOf(4, 4)});
    bot::Targets targets;
    targets.rooms = &room.map;
    room.map.update(room.w);
    require(!room.map.portalOnly(room.map.roomOf[room.w.head]), "a door leaves the room: it is not portal-only");
    targets.update(room.w);
    require(targets.pearl < 0 && targets.explore == room.w.tileOf(14, 4) && targets.explorePortalDir < 0,
            "never-seen ground nearer than the portal's far side is explored first");
    for (int i = 0; i < 4; i++)
        require(room.turn(targets).dest != bot::UNKNOWN_PORTAL, "the dragon heads for the gap, not the portal");
}

// ... and a portal nearer than never-seen ground beyond the gap wins, which the old rule took only once no
// never-seen tile was left in the region.
void nearPortalBeatsFartherNeverSeenGround() {
    Room room(true);
    // Explore grid tile (1,3) is 4 steps away by torus distance; the portal 1 and one through.
    room.place({room.w.tileOf(5, 3), room.w.tileOf(4, 3), room.w.tileOf(3, 3)});
    bot::Targets targets;
    int const crossedAfter = turnsToCross(room, targets, 4);
    require(crossedAfter == 1, "the dragon steps beside the portal and crosses it");
}

// P.portalFoldGuard: no crossing whose way back (here to our own head: no king report) doesn't fit before the fold,
// nor once the fold runs, before the last round we could still be eaten.
void noCrossingThatCannotComeBackBeforeTheFold() {
    // Two steps to the portal tile and one through, 4 to turn round, one back through, two home: 10 rounds.
    for (int roundsLeft : {9, 10}) {
        Room room;
        room.w.round = bot::foldStart() - roundsLeft;
        room.place({room.w.tileOf(4, 3), room.w.tileOf(3, 3), room.w.tileOf(2, 3)});
        bot::Targets targets;
        targets.update(room.w);
        require((targets.explorePortalDir >= 0) == (roundsLeft >= 10) && (roundsLeft >= 10 || targets.explore < 0),
                "the portal is a target only when the round trip fits before the fold");
    }
    Room room;
    room.w.round = bot::foldStart() + 5;
    room.place({room.w.tileOf(4, 3), room.w.tileOf(3, 3), room.w.tileOf(2, 3)});
    bot::Targets targets;
    targets.update(room.w);
    require(targets.explorePortalDir >= 0, "once the fold runs, a crossing with time to come back is allowed");
}

// Landings (P.portalLandingRisk): a teammate's head that comes out of a portal in our view, then vanishes from a spot
// whose every way out we see, is a bad landing; one we still see P.landingWatchRounds rounds on is a good one.
void watchedLandingsMoveTheBadChance() {
    for (bool dies : {true, false}) {
        bot::World w = test::world();
        test::body(w, {w.tileOf(2, 2), w.tileOf(2, 3)});
        int const landing = w.tileOf(8, 8);
        w.rememberEdge(w.edgeOf(landing, 3), unswbc::Edge(false, unswbc::EdgeType::PORTAL, 5));
        auto const see = [&](int head) {
            w.dragons.erase(std::remove_if(w.dragons.begin(), w.dragons.end(), [&](auto const& d) { return d.id == 7; }),
                            w.dragons.end());
            if (head >= 0) {
                test::dragon(w, 7, false, head, 1);
                w.facing[head] = 1; // heading east, out of the portal on its west edge
            }
        };
        bot::Landings landings;
        see(landing);
        landings.observe(w);
        for (int i = 1; i <= P.landingWatchRounds; i++) {
            w.round++;
            see(dies ? -1 : w.tileOf(8 + i % 2, 9)); // alive, it walks on out of the portal's edge
            landings.observe(w);
        }
        require(dies ? landings.badChance() > P.landingBadPrior : landings.badChance() < P.landingBadPrior,
                dies ? "a landing that dies in view raises the bad chance" : "one that lives lowers it");
    }
}

// P.portalBeatsPearl: in a long walled hall (x 1..30, y 2..4) whose only exit is an unknown portal on the east edge
// of (30,3), our head at (27,2) facing north is 5 steps from crossing. A pearl beyond it comes after P.reachPriorSteps
// more, so a pearl in the hall farther than that is left for the portal, and a nearer one is still the target.
void portalBeatsAFarPearlOnly() {
    int const crossSteps = 5;
    for (int margin : {-2, 2}) {
        auto w = test::world(32, 8);
        auto const inside = [](int x, int y) { return x >= 1 && x <= 30 && y >= 2 && y <= 4; };
        for (int y = 2; y <= 4; y++)
            for (int x = 1; x <= 30; x++)
                for (int dir = 0; dir < 4; dir++)
                    if (!inside(x + bot::DX[dir], y + bot::DY[dir]))
                        test::wall(w, w.tileOf(x, y), dir);
        int const portal = w.tileOf(30, 3);
        w.rememberEdge(w.edgeOf(portal, 1), unswbc::Edge(false, unswbc::EdgeType::PORTAL, 5));
        test::body(w, {w.tileOf(27, 2), w.tileOf(27, 3), w.tileOf(27, 4)});
        w.facing[w.head] = 0;
        double const portalCost = crossSteps + P.reachPriorSteps;
        int const dist = margin > 0 ? int(std::ceil(portalCost)) + margin : int(std::floor(portalCost)) + margin;
        int const pearl = w.tileOf(27 - dist, 2);
        test::pearl(w, pearl);
        bot::Targets targets;
        targets.update(w);
        if (margin > 0)
            require(targets.pearl < 0 && targets.explore == portal && targets.explorePortalDir == 1,
                    "a pearl farther than a crossing and an explorer's mean steps per pearl gives way to the portal");
        else
            require(targets.pearl == pearl, "a nearer pearl stays the target");
    }
}

void unknownPortalAwayFromTargetStaysAvoided() {
    Room room;
    room.place({room.w.tileOf(6, 3), room.w.tileOf(5, 3), room.w.tileOf(4, 3)});
    bot::Targets targets;
    test::pearl(room.w, room.w.tileOf(6, 2));
    targets.update(room.w);
    require(targets.pearl == room.w.tileOf(6, 2), "the adjacent pearl is the target");
    bot::Safety safety;
    safety.prepare(room.w);
    require(bot::evaluateMove(room.w, safety, targets, 1).score <= -P.unknownPortalPenalty,
            "with a pearl target the unknown portal keeps its avoidance penalty");
    require(bot::bestMove(room.w, safety, targets).dir == 0, "the pearl beats the portal");
}

// P.crossGate: in the opening (before P.crossModelRounds), beside the unknown portal of a room the team has fully seen,
// with no enemy head ever in sight, the leaders' crossing model crosses now, where without it, or from
// P.crossModelRounds on, or (with P.crossGateStarters) for a split child, the dragon walks to the pearl at the room's
// far end first ...
void leadersCrossOutOfASeenBarrenRoomInTheOpening() {
    for (bool gateOn : {true, false}) {
        for (int round : {P.crossModelRounds - 1, P.crossModelRounds}) {
          for (int born : {0, 3}) { // a starter, a child split off at round 3
            Room room;
            room.w.round = round;
            room.w.firstRound = born;
            room.place({room.w.tileOf(6, 3), room.w.tileOf(5, 3), room.w.tileOf(4, 3)});
            test::pearl(room.w, room.w.tileOf(2, 2));
            bot::Belief team;
            team.init(room.w);
            bot::CrossGate gate;
            gate.observe(room.w, team);
            bot::Targets targets;
            if (gateOn)
                targets.crossGate = &gate;
            bool const asks = gateOn && round < P.crossModelRounds && (born == 0 || !P.crossGateStarters);
            require((room.turn(targets).dest == bot::UNKNOWN_PORTAL) == asks,
                    "the dragon crosses at once with the gate in the opening, else heads for the pearl");
          }
        }
    }
}

// ... and where every tile of the room was seen holding a pearl (a rich home) and an enemy head was in view, the model
// says they stay, in the opening too: the dragon heads for the pearl.
void leadersStayInARichRoom() {
    Room room;
    room.w.round = P.crossModelRounds - 1;
    room.w.firstRound = 0;
    for (int y = 2; y <= 4; y++)
        for (int x = 2; x <= 6; x++)
            room.w.pearlSeen[room.w.tileOf(x, y)] = room.w.round - 1;
    room.place({room.w.tileOf(6, 3), room.w.tileOf(5, 3), room.w.tileOf(4, 3)});
    test::pearl(room.w, room.w.tileOf(2, 2));
    bot::Belief team;
    team.init(room.w);
    bot::CrossGate gate;
    gate.enemySeen = true;
    gate.observe(room.w, team);
    bot::Targets targets;
    targets.crossGate = &gate;
    require(room.turn(targets).dest != bot::UNKNOWN_PORTAL && targets.pearl == room.w.tileOf(2, 2),
            "the pearl stays the target and the dragon doesn't cross");
}

void crossingPairsThePortalAndRetargets() {
    Room room;
    room.place({room.w.tileOf(6, 3), room.w.tileOf(5, 3), room.w.tileOf(4, 3)});
    bot::Targets targets;
    require(room.turn(targets).dest == bot::UNKNOWN_PORTAL, "beside the portal with nothing left to explore, cross");

    // We land east of the partner edge at (11,10) and see that edge on our new head tile.
    int const landing = room.w.tileOf(11, 10);
    room.w.rememberEdge(room.w.edgeOf(landing, 3), unswbc::Edge(false, unswbc::EdgeType::PORTAL, 5));
    require(room.w.step(room.portalTile, 1) == landing, "after one crossing the portal leads to the landing tile");
    require(room.w.step(landing, 3) == room.portalTile, "and back");
    room.place({landing, room.portalTile, room.w.tileOf(5, 3)});
    for (int y = 8; y <= 14; y++)
        for (int x = 8; x <= 14; x++)
            room.w.lastSeen[room.w.tileOf(x, y)] = -1;
    for (int t = 0; t < room.w.N; t++)
        if (room.w.torusDist(t, landing) > 3)
            room.w.visStamp[t] = 0;
    targets.update(room.w);
    require(targets.explorePortalDir < 0, "a paired portal is no longer an explore door");
    require(room.w.xOf(targets.explore) >= 7, "the new target is on the far side, not back through the portal");
}

void knownPortalOntoBodyStaysLethal() {
    Room room;
    int const landing = room.w.tileOf(11, 10);
    room.w.rememberEdge(room.w.edgeOf(landing, 3), unswbc::Edge(false, unswbc::EdgeType::PORTAL, 5));
    room.place({room.portalTile, room.w.tileOf(5, 3), room.w.tileOf(4, 3)});
    test::dragon(room.w, 2, true, room.w.tileOf(12, 10), 3);
    room.w.occId[landing] = 2;
    room.w.occEnemy[landing] = 1;
    room.w.touched.push_back(landing);
    bot::Targets targets;
    bot::Safety safety;
    safety.prepare(room.w);
    require(bot::evaluateMove(room.w, safety, targets, 1).score <= P.lethalScore,
            "a known portal whose landing tile holds a body is a wall");
}

// A teammate standing on a portal mouth, in its own process's World: head at `head` beside portal 5 on side `side`,
// its body running straight away from that side, facing it. Returns what it plans to send this turn (plain values).
std::array<std::optional<std::uint64_t>, 4> mouthSends(int headX, int headY, int side, int round = 100) {
    auto w = test::world();
    w.round = round;
    int const head = w.tileOf(headX, headY);
    w.rememberEdge(w.edgeOf(head, side), unswbc::Edge(side % 2 == 0, unswbc::EdgeType::PORTAL, 5));
    int const back = (side + 2) % 4;
    int const neck = w.step(head, back);
    test::body(w, {head, neck, w.step(neck, back)});
    w.facing[head] = side;
    bot::Belief b;
    b.init(w);
    b.observe(w);
    bot::Gossip g;
    g.plan(w, b, head, bot::neckDir(w));
    std::array<std::optional<std::uint64_t>, 4> out{};
    for (int dir = 0; dir < 4; dir++)
        if (g.sending[dir])
            out[dir] = g.out[dir];
    return out;
}

// Scenarios (a) and (b) of the doorway probe: our crosser beside portal 5 at (6,3) would cross east (the room is
// seen and the portal is its only exit). A teammate on the partner mouth, east of the partner edge at (11,10), whose
// crossing west lands on our head, probes west through it; hearing that probe, we don't cross. The other way round,
// our probe east refuses its crossing west.
void doorwayProbeRefusesTheCrossingBothWays() {
    Room room;
    room.place({room.portalTile, room.w.tileOf(5, 3), room.w.tileOf(4, 3)});
    room.w.facing[room.portalTile] = 1;
    bot::Targets targets;
    bot::Safety safety;
    safety.prepare(room.w);
    targets.update(room.w);
    require(bot::bestMove(room.w, safety, targets).dir == 1, "with no probe heard the crosser crosses (scenario d)");

    auto const theirs = mouthSends(11, 10, 3);
    require(theirs[3].has_value(), "a head on a portal mouth sends a probe through the portal edge");
    require(!bot::decodeRecord(*theirs[3], test::wire(false)), "the probe is no belief record");
    require(!bot::decodeProbe(*theirs[3], test::wire(true)), "the other side's copy can't read it (scenario f)");
    bot::hearProbes(room.w, test::wire(false), {*theirs[3]});
    require(room.w.doorProbed[1] && !room.w.doorProbed[0] && !room.w.doorProbed[2] && !room.w.doorProbed[3],
            "the probe marks the crossing through the portal beside our head, and only it");
    require(bot::evaluateMove(room.w, safety, targets, 1).score == P.friendHeadOnScore,
            "the heard crossing scores as a head-on with a teammate");
    require(bot::bestMove(room.w, safety, targets).dir != 1, "the crosser steps elsewhere");

    // The teammate hears ours: we probe east from our mouth, and its crossing west lands on us.
    bot::Belief b;
    b.init(room.w);
    b.observe(room.w);
    bot::Gossip ours;
    ours.plan(room.w, b, room.portalTile, 3);
    require(ours.sending[1], "the crosser probes east through its own portal edge");
    auto them = test::world();
    int const mouth = them.tileOf(11, 10);
    them.rememberEdge(them.edgeOf(mouth, 3), unswbc::Edge(false, unswbc::EdgeType::PORTAL, 5));
    test::body(them, {mouth, them.tileOf(12, 10), them.tileOf(13, 10)});
    bot::hearProbes(them, test::wire(false), {ours.out[1]});
    require(them.doorProbed[3], "a crosser on the partner mouth refuses the crossing onto our head");
}

// Boundaries: a probe through our own portal edge from its other side (a teammate just west of it, probing east)
// lands beyond portal 5's far end, not where our crossing lands, and a probe older than the last round tells nothing
// about now: neither refuses our crossing west from the partner mouth.
void doorwayProbeFromAnotherMouthOrRoundIsIgnored() {
    auto w = test::world();
    int const mouth = w.tileOf(11, 10);
    w.rememberEdge(w.edgeOf(mouth, 3), unswbc::Edge(false, unswbc::EdgeType::PORTAL, 5));
    test::body(w, {mouth, w.tileOf(12, 10), w.tileOf(13, 10)});
    auto const sameEdge = mouthSends(10, 10, 1);
    require(sameEdge[1].has_value(), "the teammate across our own edge probes east through it");
    bot::hearProbes(w, test::wire(false), {*sameEdge[1]});
    require(!w.doorProbed[3], "a probe through our own edge from its far side does not refuse our crossing");

    auto const stale = mouthSends(6, 3, 1, w.round - 2);
    bot::hearProbes(w, test::wire(false), {*stale[1]});
    require(!w.doorProbed[3], "a probe from two rounds ago is ignored");
    auto const last = mouthSends(6, 3, 1, w.round - 1);
    bot::hearProbes(w, test::wire(false), {*last[1]});
    require(w.doorProbed[3], "one from the last round is heard");
}

// An open board with a paired portal: the west edge of (6,5) and the west edge of (12,12), so stepping west off (6,5)
// lands on (11,12), and (6,5), (5,5), (12,12) and (11,12) are doorways. Our 3-long dragon stands at (6,6) facing
// north, its body below, so a plain tie goes north onto the doorway.
struct Doorway {
    bot::World w = test::world();
    int doorway = w.tileOf(6, 5);

    Doorway() {
        w.setEdge(w.edgeOf(doorway, 3), bot::EDGE_PORTAL, 7);
        w.setEdge(w.edgeOf(w.tileOf(12, 12), 3), bot::EDGE_PORTAL, 7);
        test::body(w, {w.tileOf(6, 6), w.tileOf(6, 7), w.tileOf(6, 8)});
    }

    std::array<bot::MoveEval, 4> moves(bot::Targets& targets) {
        bot::Safety safety;
        safety.prepare(w);
        targets.update(w);
        return bot::evaluateMoves(w, safety, targets);
    }
};

void equalRouteOffADoorwayIsTaken() {
    Doorway d;
    test::pearl(d.w, d.w.tileOf(8, 4)); // three steps by north and by east alike
    bot::Targets targets;
    auto const moves = d.moves(targets);
    require(targets.pearl == d.w.tileOf(8, 4), "the pearl is the target");
    require(moves[0].terms[bot::TERM_DOORWAY] < 0 && moves[1].terms[bot::TERM_DOORWAY] == 0,
            "ending on the doorway is charged while the equal route east is not");
    require(bot::bestOf(moves, d.w.facing[d.w.head]).dir == 1,
            "with an equal route off it, a dragon doesn't end its move on a portal doorway");

    auto worse = moves; // the equal route now scores below the step back west (as a teammate head beside it would)
    worse[1].score = worse[3].score - 1;
    require(bot::doorwayCharges(d.w, targets, worse)[0] == 0,
            "the charge never hands the turn to a move that ends farther from the target");
}

void doorwayIsTakenWhenItIsTheOnlyGoodMoveOrTheWayAcross() {
    Doorway walled;
    test::pearl(walled.w, walled.w.tileOf(8, 4));
    test::wall(walled.w, walled.w.head, 1);
    test::wall(walled.w, walled.w.head, 3);
    bot::Targets targets;
    auto const moves = walled.moves(targets);
    require(moves[0].terms[bot::TERM_DOORWAY] == 0 && bot::bestOf(moves, walled.w.facing[walled.w.head]).dir == 0,
            "a doorway that is the only non-lethal move is taken uncharged");

    Doorway across;
    test::pearl(across.w, across.w.tileOf(10, 12)); // one step past the landing (11,12)
    bot::Targets through;
    auto const next = across.moves(through);
    require(bot::doorwayCharge(across.w, through, 0, across.doorway) == 0 && next[0].terms[bot::TERM_DOORWAY] == 0,
            "a doorway we cross from next move costs nothing");
    require(bot::bestOf(next, across.w.facing[across.w.head]).dir == 0, "the dragon steps up to cross");
}

// Trauma's start room: 11x24 in a 48x24 board (it wraps north-south), kelp east and west, two unknown portals
// in the east wall. `hole` is a room tile no teammate has seen, -1 for none.
struct Trauma {
    static bool inRoom(int x, int) { return x <= 10; }
    Room room;
    bot::Belief team;

    explicit Trauma(int holeX = -1, int holeY = -1) : room(48, 24, inRoom, {{18 * 48 + 10, 1}, {5 * 48 + 10, 1}}) {
        int const head = room.w.tileOf(5, 12);
        room.place({head, room.w.tileOf(5, 13)});
        room.seeOnlyAround(head);
        team = room.teamSaw([&](int x, int y) { return inRoom(x, y) && !(x == holeX && y == holeY); });
        if (holeX >= 0)
            room.w.lastSeen[room.w.tileOf(holeX, holeY)] = -1;
    }
};

void teamSeenSealedRoomSendsChildToPortal() {
    Trauma trauma;
    bot::Targets targets;
    targets.team = &trauma.team;
    targets.update(trauma.room.w);
    require(targets.pearl < 0 && targets.explorePortalDir == 1 && targets.explore == trauma.room.portalTile,
            "a child that has seen only its 7x7 in a room the team has seen targets the nearest unknown portal");
    int const crossedAfter = turnsToCross(trauma.room, targets, 20);
    require(crossedAfter >= 0 && crossedAfter <= 13, "and walks the 11 steps there and crosses");
}

void ownMemoryAloneKeepsExploring() {
    Trauma trauma;
    bot::Targets targets;
    targets.update(trauma.room.w);
    require(targets.explorePortalDir < 0, "without the team's belief the child explores the room as before");
}

void pearlInTeamSeenRoomBeatsPortal() {
    Trauma trauma;
    int const pearl = trauma.room.w.tileOf(3, 12); // two steps west, away from both portals
    test::pearl(trauma.room.w, pearl);
    bot::Targets targets;
    targets.team = &trauma.team;
    std::vector<int> heads;
    int const crossedAfter = turnsToCross(trauma.room, targets, 30, &heads);
    auto const ate = std::find(heads.begin(), heads.end(), pearl);
    require(ate != heads.end() && ate - heads.begin() <= 3, "the pearl in view is eaten first");
    require(crossedAfter > ate - heads.begin(), "only then does the dragon leave through a portal");
}

} // namespace

int main() {
    return test::suite({
        {"closed room walks to an unknown portal and crosses", closedRoomWalksToPortalAndCrosses},
        {"pearl sooner than the crossing is eaten first", pearlSoonerThanTheCrossingIsEatenFirst},
        {"portal-only room is left before its unseen ground", portalOnlyRoomIsLeftBeforeItsUnseenGround},
        {"portal into seen barren ground", portalIntoSeenBarrenGround},
        {"nearer never-seen ground beats the portal", nearerNeverSeenGroundBeatsThePortal},
        {"near portal beats farther never-seen ground", nearPortalBeatsFartherNeverSeenGround},
        {"no crossing that cannot come back before the fold", noCrossingThatCannotComeBackBeforeTheFold},
        {"watched landings move the bad chance", watchedLandingsMoveTheBadChance},
        {"portal beats a far pearl only", portalBeatsAFarPearlOnly},
        {"unknown portal stays avoided under a pearl target", unknownPortalAwayFromTargetStaysAvoided},
        {"leaders cross out of a seen barren room in the opening", leadersCrossOutOfASeenBarrenRoomInTheOpening},
        {"leaders stay in a rich room", leadersStayInARichRoom},
        {"crossing pairs the portal and retargets", crossingPairsThePortalAndRetargets},
        {"known portal onto a body stays lethal", knownPortalOntoBodyStaysLethal},
        {"doorway probe refuses the crossing both ways", doorwayProbeRefusesTheCrossingBothWays},
        {"doorway probe from another mouth or round is ignored", doorwayProbeFromAnotherMouthOrRoundIsIgnored},
        {"equal route off a doorway is taken", equalRouteOffADoorwayIsTaken},
        {"doorway taken when the only good move or the way across", doorwayIsTakenWhenItIsTheOnlyGoodMoveOrTheWayAcross},
        {"team-seen sealed room sends a child to its portal", teamSeenSealedRoomSendsChildToPortal},
        {"own memory alone keeps exploring", ownMemoryAloneKeepsExploring},
        {"pearl in a team-seen room beats the portal", pearlInTeamSeenRoomBeatsPortal},
    });
}
