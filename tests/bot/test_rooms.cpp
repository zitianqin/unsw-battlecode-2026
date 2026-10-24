// The room map (rooms.hpp): rooms joined by passages at least 3 wide, doors 1-2 wide, portal exits known or unknown,
// thin dead ends folded into the room they open onto, and the debug line's room keys.
#define BOT_NOTES true
// libc++'s bounds checks, so an out-of-bounds vector index traps here instead of corrupting the heap.
#define _LIBCPP_HARDENING_MODE _LIBCPP_HARDENING_MODE_EXTENSIVE

#include "support.hpp"
#include "debugline.hpp"
#include "rooms.hpp"
#include "scoring.hpp"
#include "targets.hpp"

#include <map>
#include <sstream>

using test::require;

namespace {

void portal(bot::World& w, int tile, int dir, int id) {
    w.rememberEdge(w.edgeOf(tile, dir), unswbc::Edge(false, unswbc::EdgeType::PORTAL, id));
}

// A 5x5 room (x 2..6, y 2..6) walled with kelp on test::world()'s open 16x16 board, but for a portal (id 7) on the
// east side of (6, 2) whose other end we haven't seen.
bot::World portalRoom() {
    auto w = test::world();
    auto const inside = [](int x, int y) { return x >= 2 && x <= 6 && y >= 2 && y <= 6; };
    for (int y = 2; y <= 6; y++)
        for (int x = 2; x <= 6; x++)
            for (int dir = 0; dir < 4; dir++)
                if (!inside(x + bot::DX[dir], y + bot::DY[dir]))
                    test::wall(w, w.tileOf(x, y), dir);
    portal(w, w.tileOf(6, 2), 1, 7);
    return w;
}

bot::RoomMap mapOf(bot::World const& w) {
    bot::RoomMap rooms;
    rooms.update(w);
    return rooms;
}

void portalOnlyRoom() {
    auto const w = portalRoom();
    auto const rooms = mapOf(w);
    int const room = rooms.roomOf[w.tileOf(4, 4)];
    auto const& info = rooms.rooms[room];
    require(info.tiles == 25 && info.doors == 0, "the walled 5x5 is one room with no door");
    require(info.knownPortals == 0 && info.unknownPortals == 1, "its portal's pair is unseen: an unknown exit");
    require(rooms.portalOnly(room), "every exit is a portal");
    int const outside = rooms.roomOf[w.tileOf(10, 10)];
    require(outside != room && rooms.rooms[outside].tiles == 256 - 25, "the open ground around it is another room");
    require(rooms.roomOf[w.tileOf(7, 2)] == outside && rooms.rooms[outside].unknownPortals == 1,
            "the portal's other side is an exit of the open room");
    require(!rooms.portalOnly(outside), "no door leaves the open room either, but it holds most of the map");
}

void seenDoorOpensTheRoom() {
    auto w = portalRoom();
    w.setEdge(w.edgeOf(w.tileOf(2, 4), 3), bot::EDGE_OPEN); // a 1-wide door west
    auto const rooms = mapOf(w);
    int const room = rooms.roomOf[w.tileOf(4, 4)];
    require(rooms.roomOf[w.tileOf(1, 4)] != room, "a 1-wide door doesn't join the room to the ground outside");
    require(rooms.rooms[room].doors == 1 && !rooms.portalOnly(room), "the door is an exit: not portal-only");
}

void knownPairRecordsItsLanding() {
    auto w = portalRoom();
    portal(w, w.tileOf(10, 10), 3, 7); // the pair's other end, the west side of (10, 10)
    auto const rooms = mapOf(w);
    int const room = rooms.roomOf[w.tileOf(4, 4)];
    require(rooms.rooms[room].knownPortals == 1 && rooms.rooms[room].unknownPortals == 0, "the pair is known");
    require(rooms.portalOnly(room), "a known portal is still a portal exit");
    bool found = false;
    for (auto const& exit : rooms.portalExits)
        if (exit.side == 4 * w.tileOf(6, 2) + 1) {
            found = true;
            require(exit.room == room && exit.landing == rooms.roomOf[w.tileOf(10, 10)],
                    "crossing east from (6, 2) lands in the open room around (10, 10)");
        }
    require(found, "the room's portal side is listed");
}

void deadEndFoldsIntoTheRoom() {
    auto w = portalRoom();
    // A 1-tile nook west of (2, 4): open into the room, kelp on its other three sides.
    int const nook = w.tileOf(1, 4);
    w.setEdge(w.edgeOf(nook, 1), bot::EDGE_OPEN);
    for (int dir : {0, 2, 3})
        test::wall(w, nook, dir);
    auto rooms = mapOf(w);
    int const room = rooms.roomOf[w.tileOf(4, 4)];
    require(rooms.roomOf[nook] != room && rooms.rooms[rooms.roomOf[nook]].tiles == 1, "the nook is a room of its own");
    require(rooms.rooms[room].doors == 0 && rooms.portalOnly(room), "a door into a dead end is no way out");
    require(rooms.rooms[rooms.roomOf[nook]].doors == 1 && rooms.portalOnly(rooms.roomOf[nook]),
            "from the nook the room is the way out, and the room's area is left only by its portal");

    w.setEdge(w.edgeOf(nook, 3), bot::EDGE_OPEN); // the nook's far side opens: a corridor out
    rooms.update(w);
    int const opened = rooms.roomOf[w.tileOf(4, 4)];
    require(rooms.rooms[opened].doors == 1 && !rooms.portalOnly(opened), "a corridor out is a door");
    require(rooms.rooms[rooms.roomOf[nook]].doors == 2, "the 1-wide corridor joins the room to the ground outside");
}

void portalFreePocketKeepsItsRoom() {
    // The walled 5x5 without its portal, a 1-wide door west: a dead-end pocket off the open ground.
    auto w = portalRoom();
    w.setEdge(w.edgeOf(w.tileOf(6, 2), 1), bot::EDGE_KELP);
    w.setEdge(w.edgeOf(w.tileOf(2, 4), 3), bot::EDGE_OPEN);
    auto const rooms = mapOf(w);
    int const pocket = rooms.roomOf[w.tileOf(4, 4)], outside = rooms.roomOf[w.tileOf(10, 10)];
    require(pocket != outside && rooms.rooms[pocket].tiles == 25, "the pocket is still its own room");
    require(rooms.rooms[pocket].doors == 1 && !rooms.portalOnly(pocket), "its door is its way out");
    require(rooms.rooms[outside].doors == 1, "a pocket 3+ wide is a room: its door counts from outside too");
}

void portalAtTheBackOfAnAlcove() {
    auto w = portalRoom();
    // A 1-tile alcove west of (2, 4), open into the room, kelp north and south, a portal (id 8) at its back.
    int const alcove = w.tileOf(1, 4);
    w.setEdge(w.edgeOf(alcove, 1), bot::EDGE_OPEN);
    test::wall(w, alcove, 0);
    test::wall(w, alcove, 2);
    portal(w, alcove, 3, 8);
    auto const rooms = mapOf(w);
    int const room = rooms.roomOf[w.tileOf(4, 4)];
    require(rooms.rooms[room].doors == 0 && rooms.rooms[room].unknownPortals == 2,
            "the alcove's portal is the room's second exit, reached through a dead end");
    require(rooms.portalOnly(room) && rooms.portalOnly(rooms.roomOf[alcove]), "every way out is a portal");
}

// Two halves of the board, walls between columns 7|8 and 15|0 with a gap of `gap` tiles from y = 4 in the first,
// and an unknown portal in open ground inside each half.
bot::World halves(int gap) {
    auto w = test::world();
    for (int y = 0; y < 16; y++) {
        test::wall(w, w.tileOf(0, y), 3);
        if (y < 4 || y >= 4 + gap)
            test::wall(w, w.tileOf(7, y), 1);
    }
    portal(w, w.tileOf(3, 12), 0, 20);
    portal(w, w.tileOf(11, 12), 0, 21);
    return w;
}

void passageWidthSplitsRooms() {
    for (int gap : {1, 2}) {
        auto const w = halves(gap);
        auto const rooms = mapOf(w);
        int const left = rooms.roomOf[w.tileOf(3, 8)], right = rooms.roomOf[w.tileOf(11, 8)];
        require(left != right, "a passage 1-2 wide is a door between two rooms");
        require(rooms.rooms[left].doors == 1 && rooms.rooms[right].doors == 1, "each room has the other as its door");
        require(rooms.rooms[left].unknownPortals == 1, "an open-ground portal counts once for its room");
    }
    auto const w = halves(3);
    auto const rooms = mapOf(w);
    require(rooms.roomOf[w.tileOf(3, 8)] == rooms.roomOf[w.tileOf(11, 8)], "a passage 3 wide joins the halves");
}

void wideRoomLeftWithoutTiles() {
    // Two kelp edges on a 5x5 board (it wraps): the open blocks form two wide rooms, and the first covers every tile,
    // so the second, whose tiles all go to the lower room, is left with none.
    auto w = test::world(5, 5);
    test::wall(w, w.tileOf(1, 2), 2);
    test::wall(w, w.tileOf(3, 0), 1);
    auto const rooms = mapOf(w);
    require(rooms.rooms.size() == 1 && rooms.rooms[0].tiles == 25 && rooms.rooms[0].areaTiles == 25,
            "the empty wide room gets no id: one room holds the board");
    require(std::all_of(rooms.roomOf.begin(), rooms.roomOf.end(), [](int r) { return r == 0; }), "every tile in it");
}

void rebuildsWhenAnEdgeChanges() {
    auto w = portalRoom();
    bot::RoomMap rooms;
    rooms.update(w);
    require(rooms.portalOnly(rooms.roomOf[w.tileOf(4, 4)]), "portal-only before the door is seen");
    w.setEdge(w.edgeOf(w.tileOf(2, 4), 3), bot::EDGE_OPEN);
    rooms.update(w);
    require(!rooms.portalOnly(rooms.roomOf[w.tileOf(4, 4)]), "the same map object sees the new door");
}

std::map<std::string, std::string> fields(std::string const& line) {
    std::istringstream in(line);
    std::map<std::string, std::string> out;
    std::string key, value;
    while (in >> key >> value)
        out[key] = value;
    return out;
}

void debugLineCarriesTheRoom() {
    auto w = portalRoom();
    test::body(w, {w.tileOf(4, 4), w.tileOf(3, 4)});
    bot::notes.clear();
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    targets.update(w);
    auto const moves = bot::evaluateMoves(w, safety, targets);
    auto line = fields(bot::debugLine(w, safety, targets, moves, bot::bestOf(moves), {.stepped = true}));
    require(!line.count("room") && !line.count("rportal"), "no room map, no room keys");
    bot::RoomMap rooms;
    rooms.update(w);
    line = fields(bot::debugLine(w, safety, targets, moves, bot::bestOf(moves), {.stepped = true, .rooms = &rooms}));
    require(line["room"] == std::to_string(rooms.roomOf[w.head]) && line["rtiles"] == "25", "our head's room");
    require(line["rexits"] == "0/0/1" && line["rportal"] == "1", "no door, one unknown portal: portal-only");
}

} // namespace

// P.leaveJam (jamroom.hpp): a 9x5 walled garden (x 2..10, y 2..6) with a 2-wide door west of (2, 3) and (2, 4), us at
// (3, 4) two steps from it, `mates` teammates' heads 4 long deeper in and a pearl at (4, 3). The room is jammed when
// our heads' lengths + P.spaceMargin each, and the pearl, pass its 45 tiles.
struct Garden {
    bot::World w = test::world();
    bot::RoomMap rooms;
    bot::Targets targets;

    explicit Garden(int mates, bool mateAtDoor = false) {
        auto const inside = [](int x, int y) { return x >= 2 && x <= 10 && y >= 2 && y <= 6; };
        for (int y = 2; y <= 6; y++)
            for (int x = 2; x <= 10; x++)
                for (int dir = 0; dir < 4; dir++)
                    if (!inside(x + bot::DX[dir], y + bot::DY[dir]) && !(x == 2 && dir == 3 && (y == 3 || y == 4)))
                        test::wall(w, w.tileOf(x, y), dir);
        test::body(w, {w.tileOf(3, 4), w.tileOf(4, 4), w.tileOf(5, 4), w.tileOf(6, 4)});
        for (int i = 0; i < mates; i++) // row 6, then row 2, ids above ours
            test::dragon(w, 10 + i, false, w.tileOf(3 + i % 8, i < 8 ? 6 : 2), 4);
        if (mateAtDoor)
            test::dragon(w, 0, false, w.tileOf(2, 4), 4); // beside the door
        test::pearl(w, w.tileOf(4, 3));
        rooms.update(w);
        targets.rooms = &rooms;
        targets.update(w);
    }
};

// The fewest teammates that jam the garden with us: (1 + mates) * (4 + P.spaceMargin) + 1 pearl > 45.
int jamMates() {
    int mates = 0;
    while ((1 + mates) * (4 + P.spaceMargin) + 1 <= 45)
        mates++;
    return mates;
}

void jammedRoomNearestLeaves() {
    Garden g(jamMates());
    require(g.targets.pearl < 0 && g.targets.exploreFromJam, "jammed, we are nearest the door: the pearl is dropped");
    require(g.targets.explore == g.w.tileOf(1, 4), "the target is one step past the door, where our path leaves");
    for (int t : g.w.body)
        g.w.occId[t] = -1;
    std::erase_if(g.w.dragons, [&](auto const& d) { return d.id == g.w.myId; });
    test::body(g.w, {g.w.tileOf(0, 4), g.w.tileOf(1, 4), g.w.tileOf(2, 4), g.w.tileOf(3, 4)});
    test::pearl(g.w, g.w.tileOf(2, 3)); // just inside the door, 3 steps from our head
    g.targets.update(g.w);
    require(g.targets.pearl < 0, "out of the door, we don't walk back in for a pearl while it would jam again");
    Garden const mate(jamMates(), true);
    require(mate.targets.pearl == mate.w.tileOf(4, 3) && !mate.targets.exploreFromJam,
            "a teammate nearer the door leaves first; we keep our pearl");
}

void roomThatFitsKeepsItsPearls() {
    Garden const g(jamMates() - 1);
    require(g.targets.pearl == g.w.tileOf(4, 3) && !g.targets.exploreFromJam,
            "one teammate fewer and the garden carries us all: nobody leaves");
}

int main() {
    return test::suite({
        {"a walled room with one portal is portal-only", portalOnlyRoom},
        {"a seen 1-wide door opens it", seenDoorOpensTheRoom},
        {"a known pair records where it lands", knownPairRecordsItsLanding},
        {"a dead end folds into its room", deadEndFoldsIntoTheRoom},
        {"a portal-free pocket keeps its room", portalFreePocketKeepsItsRoom},
        {"a portal at the back of an alcove", portalAtTheBackOfAnAlcove},
        {"passages under 3 wide split rooms", passageWidthSplitsRooms},
        {"a wide room left without tiles builds in bounds", wideRoomLeftWithoutTiles},
        {"the map rebuilds when an edge changes", rebuildsWhenAnEdgeChanges},
        {"the debug line carries the room", debugLineCarriesTheRoom},
        {"the head nearest a jammed room's door leaves it", jammedRoomNearestLeaves},
        {"a room that carries our heads keeps its pearls", roomThatFitsKeepsItsPearls},
    });
}
