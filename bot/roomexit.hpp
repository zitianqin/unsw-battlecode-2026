#pragma once

#include "rooms.hpp"
#include "state.hpp"

#include <climits>

namespace bot {

// Leaving a room that only portals leave (P.portalRoomExit, I446/I825). Vision stops at a portal, so a start room
// whose exits are all portals looks closed, and before this the dragons left it only once it held no tile left to
// see (Targets::chooseExplore's fallback), with pearls in the room pre-empting even that. Here, once our room map
// (rooms.hpp) says our room's area is portalOnly, each unknown portal edge out of that area goes to the dragon
// nearest it by path among those in view in the area (ties to the lower id), and Targets::pickTarget sends that
// dragon through it unless its pearl target is eaten sooner than it could cross. Every other dragon explores as
// before. The room map reads this dragon's edge memory (mirrored writes and the kelp teammates share over sonar,
// P.shareKelpEdges, included), so it fires once every wall of the room is known: unseen edges count as open. With P.portalRoomExitStarters only a
// dragon alive since round 0 takes an exit, and split children (ids from 2 x our round-0 unit count on: ids go in map
// order from 0, both teams start with as many dragons, and a child takes the next free id) don't count as nearer. A
// child starts with no memory, so the portal pocket it is born in reads as sealed: in E0293's SPRT, on the portalrooms
// maps where only children fired this rule the candidate did worse against scout (17 better, 34 worse trials).
struct RoomExit {
    int tile = -1; // the tile beside the unknown portal edge we take, -1 none
    int dir = -1;  // the direction through it from `tile`
    int steps = 0; // our path to `tile` plus the crossing step
    DistField field;

    // Picks this turn's portal edge for us, if any; returns whether there is one.
    bool choose(World const& w, RoomMap const& rooms) {
        tile = dir = -1;
        int const room = rooms.roomOf[w.head];
        if (!rooms.portalOnly(room) || (P.portalRoomExitStarters && w.firstRound != 0))
            return false;
        int const firstChild = P.portalRoomExitStarters ? 2 * w.bornUnits : INT_MAX;
        int const area = rooms.rooms[room].area;
        int const depth = rooms.rooms[area].areaTiles;
        int best = DistField::FAR;
        for (RoomMap::PortalExit const& exit : rooms.portalExits) {
            if (exit.landing >= 0 || rooms.rooms[exit.room].area != area)
                continue;
            int const t = exit.side / 4, d = exit.side % 4;
            field.build(w, t, depth);
            int const ours = field.at(w.head);
            if (ours >= best)
                continue;
            bool nearer = false;
            for (auto const& mate : w.dragons) {
                if (mate.enemy || mate.id == w.myId || mate.id >= firstChild || mate.head < 0 ||
                    rooms.rooms[rooms.roomOf[mate.head]].area != area)
                    continue;
                int const theirs = field.at(mate.head);
                nearer = nearer || theirs < ours || (theirs == ours && mate.id < w.myId);
            }
            if (nearer)
                continue;
            best = ours;
            tile = t;
            dir = d;
        }
        steps = best + 1;
        return tile >= 0;
    }
};

} // namespace bot
