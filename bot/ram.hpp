#pragma once

#include "rammodel.hpp"
#include "safety.hpp"
#include "state.hpp"

#include <algorithm>
#include <vector>

namespace bot {

// A ram: the steps (dir indices) that end on an enemy head, and that head's tile and visible length.
struct Ram {
    std::vector<int> steps; // empty: no ram
    int victim = -1;
    int victimLen = 0;
};

// Our head-on reach without pearls, used by huntDirection to leave reachable heads to findRam (whose gate decides).
// P.longSprintRams changes only our reach; enemy estimates use enemy_reach.hpp.
inline int ownRamReach(int length) {
    return std::min(P.longSprintRams ? P.ownRamSteps : P.maxRamSteps, std::max(1, length - 1));
}

// The steps findRam searches: ownRamReach extended by pearls within the legacy P.maxRamSteps cap.
inline int ramSearchSteps(World const& w) {
    return P.longSprintRams ? std::min(P.ownRamSteps, std::max(P.maxRamSteps, w.length - 1)) : P.maxRamSteps;
}

// Every enemy head we can reach this turn in one sprint, each by its fewest-steps path (the first found in N, E, S,
// W depth-first order), in the order those paths were found. Nothing else moves during our turn, so each is a
// certain head-on: both dragons die. Searches over visible empty tiles (bodies, other heads, kelp, unknown portals
// and our own path all block) up to ramSearchSteps. Each step after the first costs a segment and needs length >= 3
// before it; a pearl on the way pays one back.
inline std::vector<Ram> ramOptions(World const& w) {
    std::vector<Ram> found;
    std::vector<int> order; // found[k]'s path's discovery number
    int const maxSteps = ramSearchSteps(w);
    int discovered = 0;
    std::vector<int> path;
    std::vector<int> tiles; // tiles entered so far: our new body
    auto const search = [&](auto const& self, int at, int length) -> void {
        int const stepNo = static_cast<int>(path.size()) + 1;
        if (stepNo > maxSteps || (stepNo >= 2 && length < 3))
            return;
        for (int dir = 0; dir < 4; dir++) {
            int const to = w.step(at, dir);
            if (to < 0 || !w.visible(to))
                continue;
            if (w.occId[to] >= 0) {
                SeenDragon const* them = w.occHead[to] && w.occEnemy[to] ? w.dragonAt(to) : nullptr;
                if (!them)
                    continue;
                auto const k = std::find_if(found.begin(), found.end(), [&](Ram const& r) { return r.victim == to; });
                if (k == found.end()) {
                    found.push_back({path, to, them->visibleLen});
                    found.back().steps.push_back(dir);
                    order.push_back(discovered);
                } else if (stepNo < static_cast<int>(k->steps.size())) {
                    k->steps = path;
                    k->steps.push_back(dir);
                    order[k - found.begin()] = discovered;
                }
                discovered++;
                continue;
            }
            bool revisits = false;
            for (int t : tiles)
                revisits = revisits || t == to;
            if (revisits)
                continue;
            int const next = length + (w.hasPearlHere(to) ? 1 : 0) - (stepNo >= 2 ? 1 : 0);
            path.push_back(dir);
            tiles.push_back(to);
            self(self, to, next);
            path.pop_back();
            tiles.pop_back();
        }
    };
    search(search, w.head, w.length);
    // Discovery order of each victim's kept path, so ties go to the earliest path as a single search would pick.
    std::vector<int> idx(found.size());
    for (std::size_t k = 0; k < idx.size(); k++)
        idx[k] = static_cast<int>(k);
    std::sort(idx.begin(), idx.end(), [&](int a, int b) { return order[a] < order[b]; });
    std::vector<Ram> sorted;
    sorted.reserve(found.size());
    for (int k : idx)
        sorted.push_back(std::move(found[k]));
    return sorted;
}

// The best ram worth starting (ramWorth) among ramOptions. With P.ramModel the highest expected swing (ramSwing),
// else the longest victim; then the fewest steps, then the path found first. main() plays the ram ahead of every
// other move, one-step rams included.
inline Ram findRam(World const& w, Safety const& safety) {
    if (std::none_of(w.dragons.begin(), w.dragons.end(), [&](SeenDragon const& d) {
            return d.enemy && ramCandidate(w, safety, d.visibleLen);
        }))
        return {};
    Ram best;
    double bestValue = 0;
    for (Ram& r : ramOptions(w)) {
        if (!ramWorth(w, safety, r.victim, r.victimLen, r.steps))
            continue;
        double const value = P.ramModel ? ramSwing(w, safety, r.victim, r.victimLen) : r.victimLen;
        if (best.steps.empty() || value > bestValue || (value == bestValue && r.steps.size() < best.steps.size())) {
            best = std::move(r);
            bestValue = value;
        }
    }
    return best;
}

} // namespace bot
