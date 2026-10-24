#pragma once

#include "params.hpp"
#include "rooms.hpp"
#include "state.hpp"

#include <vector>

namespace bot {

// Leaving a jammed room (P.leaveJam, I877). Our short dragons crowd walled rooms (a pearl garden behind a 1-2 wide
// door) until the room can't hold them, and then each move the scorer rates best is still closed: the jam forms
// before any move can help. Here, when our room's area (rooms.hpp: the room and the dead ends folded into it) holds
// more than its free tiles can carry, the one of our heads in it nearest an exit by path leaves through that exit,
// so the room's count falls one dragon at a time. Only a wide room's area is asked: a thin one (a corridor 1-2 wide
// between doors) is ground dragons pass through, their bodies trailing out of it, not a room they fill. Every dragon
// asks each turn from what it sees:
// - load: over our heads in view in the area (ours at our length, teammates' at their visible length, a lower bound),
//   length + P.spaceMargin each, plus the pearls in view in the area (whoever eats them grows in the room);
// - free tiles: the area's tiles less those another body in view stands on (an enemy's, or a teammate's whose head
//   is outside the area);
// - exits: an open (or unseen) edge or a known portal from an area tile to a tile of another area; unknown portals
//   are left to the room exit (roomexit.hpp);
// - the leaver: the head in the area with the fewest steps to an exit (steps through the area, kelp only: bodies
//   move), the lower id on a tie, leaving out the king (the one we believe we are, or the one reported to us), who
//   keeps its pearls.
// The leaver keeps skipping the area's pearls once out (bar()) while going back in would jam it again, so it doesn't
// walk straight back for the nearest pearl.
struct JamLeave {
    int tile = -1;  // the tile one step past the exit we leave by, -1 none
    int steps = 0;  // our steps to it
    int area = -1;  // the jammed area we are leaving
    int heads = 0;  // our heads in view in our area this turn ...
    int load = 0;   // ... their load ...
    int free = 0;   // ... and the area's free tiles (0 when not asked)
    std::vector<int> dist, exitOf;

    struct Head {
        int id, head, len;
    };
    std::vector<Head> ours;

    int anchor = -1; // a tile of the jammed area we were last the leaver of, -1 none
    int barred = -1; // bar()'s answer this turn: the area whose pearls we skip, -1 none

    // Call before choosing a pearl: once we have left a jammed area as its leaver, bars its pearls (sets `barred`) for
    // as long as it would be jammed again with us back in it at our length, by what we see of it from outside; then
    // forgets it. Inside it, choose() decides.
    void bar(World const& w, RoomMap const& rooms) {
        barred = -1;
        if (anchor < 0)
            return;
        int const left = rooms.rooms[rooms.roomOf[anchor]].area;
        if (left == rooms.rooms[rooms.roomOf[w.head]].area)
            return;
        if (rooms.rooms[left].wide && measure(w, rooms, left) > 0)
            barred = left;
        else
            anchor = -1;
    }

    // Asks the rule for this turn; `king`: we believe we are king; `kingId`: the king reported to us, -1 none.
    // Returns whether we are the leaver (tile, steps and area set, and the area remembered for bar()).
    bool choose(World const& w, RoomMap const& rooms, bool king, int kingId) {
        tile = -1;
        steps = heads = load = free = 0;
        area = rooms.rooms[rooms.roomOf[w.head]].area;
        if (!rooms.rooms[area].wide || measure(w, rooms, area) <= 0 || king)
            return false;
        auto const areaOf = [&](int t) { return rooms.rooms[rooms.roomOf[t]].area; };

        // Steps from each area tile to its nearest exit, and which outside tile that exit leads to.
        int const N = w.N;
        dist.assign(N, DistField::FAR);
        exitOf.assign(N, -1);
        std::vector<int>& queue = queueScratch;
        queue.clear();
        for (int t = 0; t < N; t++) {
            if (areaOf(t) != area)
                continue;
            for (int d = 0; d < 4; d++)
                if (int const to = w.step(t, d); to >= 0 && areaOf(to) != area && dist[t] > 1) {
                    dist[t] = 1;
                    exitOf[t] = to;
                }
            if (dist[t] == 1)
                queue.push_back(t);
        }
        for (std::size_t at = 0; at < queue.size(); at++) {
            int const cur = queue[at];
            for (int d = 0; d < 4; d++)
                if (int const to = w.step(cur, d); to >= 0 && areaOf(to) == area && dist[to] > dist[cur] + 1) {
                    dist[to] = dist[cur] + 1;
                    exitOf[to] = exitOf[cur];
                    queue.push_back(to);
                }
        }
        Head const* leaver = nullptr;
        for (Head const& h : ours) {
            if (h.id == kingId || dist[h.head] >= DistField::FAR)
                continue;
            if (!leaver || dist[h.head] < dist[leaver->head] ||
                (dist[h.head] == dist[leaver->head] && h.id < leaver->id))
                leaver = &h;
        }
        if (!leaver || leaver->id != w.myId)
            return false;
        tile = exitOf[w.head];
        steps = dist[w.head];
        anchor = w.head;
        return true;
    }

private:
    std::vector<int> queueScratch;

    // Sets heads, load and free for area `a` from what we see, counting us in it at our length (ours' first entry);
    // returns load - free, positive when the area is jammed.
    int measure(World const& w, RoomMap const& rooms, int a) {
        auto const areaOf = [&](int t) { return rooms.rooms[rooms.roomOf[t]].area; };
        ours.assign(1, {w.myId, w.head, w.length});
        for (auto const& d : w.dragons)
            if (!d.enemy && d.id != w.myId && d.head >= 0 && areaOf(d.head) == a)
                ours.push_back({d.id, d.head, d.visibleLen});
        heads = static_cast<int>(ours.size());
        int pearls = 0, taken = 0;
        for (int t : w.touched) {
            if (areaOf(t) != a)
                continue;
            pearls += w.pearlNow[t];
            if (w.occId[t] >= 0 && !isOurs(w.occId[t]))
                taken++;
        }
        load = pearls;
        for (Head const& h : ours)
            load += h.len + P.spaceMargin;
        free = rooms.rooms[a].areaTiles - taken;
        return load - free;
    }

    bool isOurs(int id) const {
        for (Head const& h : ours)
            if (h.id == id)
                return true;
        return false;
    }
};

} // namespace bot
