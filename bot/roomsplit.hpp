#pragma once

#include "helper.hpp"
#include "params.hpp"
#include "safety.hpp"
#include "state.hpp"

#include <cstdlib>

namespace bot {

// Breeding in closed rooms (P.closedRoomSplits). A room whose only ways out are portals (Queen Of Spades' pockets,
// Portals' rooms) is closed to a split child: it is a new process with no memory, so a portal whose far end it
// can't see is a wall to it, and it farms, splits and sheds inside until the room is full of our 2-long dragons
// (E0247: most of our pocket deaths on portal-room maps were such children). So an ordinary split or a trapped-tail
// shed whose child starts in such a room goes ahead only when the room's free tiles hold the two halves for the
// rounds left: both bodies, P.spaceMargin tiles each, and what each will eat by the last round at our intake rate.
// A child that knows a way out (open ground, or a portal pair both of whose ends it sees at birth) splits as before,
// so a farmer can still breed children that walk out. Split-outs (split.hpp's splitOutLength) and threat sheds
// (shedLength) are not asked: without them the dragon dies.

// Whether tile t is within a dragon's 7x7 view when its head is at `head`.
inline bool inView(World const& w, int head, int t) {
    int dx = std::abs(w.xOf(t) - w.xOf(head)), dy = std::abs(w.yOf(t) - w.yOf(head));
    dx = std::min(dx, w.W - dx);
    dy = std::min(dy, w.H - dy);
    return dx <= unswbc::Constants::VISION_RADIUS && dy <= unswbc::Constants::VISION_RADIUS;
}

// The ground a dragon born with its head at `from` knows a way through, past kelp alone (bodies move on): steps over
// open and unseen edges (assumed open, as everywhere in the bot), and through a portal only when both of its ends
// are within its view from `from` and we know the pair. Counted up to `cap` tiles; `free` leaves out tiles another
// dragon's body stands on now, `holdsHead` says whether our head is in it.
struct Room {
    int tiles = 0;
    int free = 0;
    bool holdsHead = false;
};

inline Room roomOf(World const& w, Safety& safety, int from, int cap) {
    Room room;
    safety.seenStamp++;
    safety.queue.clear();
    safety.queue.push_back(from);
    safety.seen[from] = safety.seenStamp;
    for (std::size_t at = 0; at < safety.queue.size() && static_cast<int>(safety.queue.size()) < cap; at++) {
        int const cur = safety.queue[at];
        for (int dir = 0; dir < 4 && static_cast<int>(safety.queue.size()) < cap; dir++) {
            int const to = w.step(cur, dir);
            if (to < 0 || safety.seen[to] == safety.seenStamp)
                continue;
            if (w.edgeKind[w.edgeOf(cur, dir)] == EDGE_PORTAL && !(inView(w, from, cur) && inView(w, from, to)))
                continue;
            safety.seen[to] = safety.seenStamp;
            safety.queue.push_back(to);
        }
    }
    room.tiles = static_cast<int>(safety.queue.size());
    for (int t : safety.queue) {
        room.free += w.occId[t] < 0 || w.occId[t] == w.myId ? 1 : 0;
        room.holdsHead = room.holdsHead || t == w.head;
    }
    return room;
}

// True when a split of `child` segments whose child's head starts at `childHead` may go ahead: the child's room
// reaches fillCap tiles (open ground, or a portal it sees both ends of), or its free tiles hold what the split
// leaves in it for the rounds left: the child, and the parent too when our head is in the room, each with
// P.spaceMargin tiles to turn in and `rate` pearls a round (our intake, capfeed.hpp's Intake::rate) of growth.
inline bool roomHoldsSplit(World const& w, Safety& safety, int childHead, int child, double rate) {
    if (!P.closedRoomSplits)
        return true;
    int const cap = fillCap(w);
    Room const room = roomOf(w, safety, childHead, cap);
    if (room.tiles >= cap)
        return true;
    int const halves = room.holdsHead ? 2 : 1;
    int const roundsLeft = std::max(0, unswbc::Constants::MAX_ROUNDS - 1 - w.round);
    double const need = (room.holdsHead ? w.length : child) + halves * (P.spaceMargin + rate * roundsLeft);
    return room.free >= need;
}

} // namespace bot
