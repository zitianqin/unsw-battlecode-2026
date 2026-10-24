#pragma once

#include "params.hpp"
#include "state.hpp"

#include <algorithm>
#include <vector>

namespace bot {

// Portal landings this dragon has watched this game and how they ended, for the price of crossing an unknown portal
// (P.portalLandingRisk, Targets::portalScore). Vision stops at a portal, so every crossing lands blind, a known
// portal's too. Two kinds of crossing are watched: our own (we survived it once we are still alive
// P.landingWatchRounds rounds on; a dragon that dies records nothing, so our own record only ever holds survivals),
// and a teammate's head coming out of a portal in our view (it survived when we still see it P.landingWatchRounds
// rounds on, and died when it vanished from a spot whose every way out we see, none of them a portal). The chance a
// crossing goes bad is a Beta posterior: P.landingBadPrior worth P.landingPriorWeight crossings, plus what we saw.
// Call observe() once per turn after World::observe. A split child starts with no memory, from the prior.
struct Landings {
    struct Watch {
        int id;        // the crosser's dragon id (ours for our own crossing)
        int round;     // the round we saw it land
        int head;      // its head when last seen
        int len;       // ... and its visible length then
        int lastSeen;  // the round of that sighting
        int entry;     // the portal edge it came out of (going back through it is into its own neck)
    };
    std::vector<Watch> watching;
    int good = 0;
    int bad = 0;
    int prevHead = -1;

    // The chance a blind landing goes bad, from the prior and the landings we saw end.
    double badChance() const {
        return (P.landingBadPrior * P.landingPriorWeight + bad) / (P.landingPriorWeight + good + bad);
    }

    // True when the head at tile t (facing its last move) may have come out of a portal on that move: the edge behind
    // it is a portal (observe also asks that our own head stood across it last turn, and a teammate's show no neck).
    static bool cameThroughPortal(World const& w, int t) {
        int const back = (w.facing[t] + 2) % 4;
        return w.edgeKind[w.edgeOf(t, back)] == EDGE_PORTAL;
    }

    void observe(World const& w) {
        for (auto it = watching.begin(); it != watching.end();) {
            bool const ours = it->id == w.myId;
            SeenDragon const* d = ours ? nullptr : seenById(w, it->id);
            if (ours || (d && d->head >= 0)) {
                it->head = ours ? w.head : d->head;
                it->len = ours ? w.length : d->visibleLen;
                it->lastSeen = w.round;
                if (w.round - it->round >= P.landingWatchRounds) {
                    good++;
                    it = watching.erase(it);
                    continue;
                }
                ++it;
                continue;
            }
            if (it->lastSeen == w.round - 1 && diedInView(w, it->head, it->len, it->entry))
                bad++; // else it left our view: no outcome
            it = watching.erase(it);
        }
        if (prevHead >= 0 && prevHead != w.head && w.round > w.firstRound && cameThroughPortal(w, w.head) &&
            w.step(w.head, (w.facing[w.head] + 2) % 4) == prevHead)
            startWatch(w.myId, w);
        prevHead = w.head;
        for (auto const& d : w.dragons)
            if (!d.enemy && d.id != w.myId && d.head >= 0 && d.visibleLen == 1 && cameThroughPortal(w, d.head))
                startWatch(d.id, w, d.head);
    }

private:
    static SeenDragon const* seenById(World const& w, int id) {
        for (auto const& d : w.dragons)
            if (d.id == id)
                return &d;
        return nullptr;
    }

    void startWatch(int id, World const& w, int head = -1) {
        for (auto const& watch : watching)
            if (watch.id == id)
                return;
        int const at = head < 0 ? w.head : head;
        watching.push_back({id, w.round, at, head < 0 ? w.length : 1, w.round, w.edgeOf(at, (w.facing[at] + 2) % 4)});
    }

    // A dragon whose head stood at `head` last round, `len` long as far as we saw, and is nowhere in view now died,
    // when every tile its move could reach (a sprint of up to len - 1 steps, at least one step) is in view and none of
    // those steps crosses a portal but the one it came out of, `entry` (going back through it is into its own neck).
    static bool diedInView(World const& w, int head, int len, int entry) {
        if (!w.visible(head))
            return false;
        int const reach = std::max(1, len - 1);
        for (int dy = -reach; dy <= reach; dy++) {
            for (int dx = std::abs(dy) - reach; dx <= reach - std::abs(dy); dx++) {
                int const t = w.tileOf((w.xOf(head) + dx + w.W) % w.W, (w.yOf(head) + dy + w.H) % w.H);
                if (!w.visible(t))
                    return false;
                for (int dir = 0; dir < 4; dir++) {
                    int const e = w.edgeOf(t, dir);
                    if (e != entry && w.edgeKind[e] == EDGE_PORTAL)
                        return false;
                }
            }
        }
        return true;
    }
};

} // namespace bot
