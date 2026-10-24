#pragma once

#include "claims.hpp"
#include "params.hpp"
#include "state.hpp"

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <vector>

namespace bot {

// The opening race for the middle of the map (I294). Where it reaches its target by round P.raceRounds (raceTarget), a
// dragon born in round 0 (a starter, or a child it split off at once, so born at home) heads for the
// ground between the teams instead of walking the edge of its home area. Children born later start wherever their
// parent's tail was, often past the middle, so their birth tile says nothing about where home is: they don't race.
//
// Every axis of the torus has two lines a mirror of that axis leaves in place: the middle line and the seam where the
// map wraps. The race aims, on each axis, at the one farther from the tile where this dragon was born (our home sits
// near the other one), and skips a line whose seen crossings are all kelp (at least P.raceWallSeen of them): the
// halves meet the long way round there. On an axis no live symmetry candidate flips (symmetry.hpp), a map may be
// shifted by any amount, so neither line means anything: the target keeps our birth coordinate there. The target is
// the tile where the two chosen lines cross, on the side of each line nearer our birth tile. Built from where we
// started and the kelp we have seen rather than the map file's middle, it stays right on a map shifted by half a side,
// which keeps the mirror but moves the middle to the seam.
//
// Targets::update calls raceTarget each turn: while it returns a tile (reachable and never yet in our view), pearl
// targets keep only a pearl in view (or a fast tile in view) and anything nearer the race target than our head, less
// the home food raceDefers passes up, and a dragon with no pearl target walks to the race target instead of
// exploring. Once we have seen the target the race is over for good.

namespace race_detail {

// Which line of an axis of `size` tiles: 0 the middle, 1 the seam.
inline double linePos(int size, int line) { return line == 0 ? (size - 1) / 2.0 : -0.5; }

// Torus distance along one axis between a tile coordinate and a line position.
inline double axisDist(double a, double b, int size) {
    double const d = std::fmod(std::abs(a - b), static_cast<double>(size));
    return std::min(d, size - d);
}

// The coordinate on `line` nearest `born`: the line's own tile on an odd middle, else the nearer of the two tiles
// beside it.
inline int lineCoord(int size, int line, int born) {
    int a = size - 1, b = 0; // the seam lies between the last tile and the first
    if (line == 0) {
        a = (size - 1) / 2;
        b = size / 2;
    }
    return axisDist(born, a, size) <= axisDist(born, b, size) ? a : b;
}

// Whether every seen crossing of the line is kelp, with at least P.raceWallSeen seen. Axis 0 is x (a vertical line,
// crossed through the west sides of one column), axis 1 is y (a horizontal line, crossed through the north sides of
// one row). On an odd middle that column or row is the line itself, whose west (north) side mirrors its east (south).
inline bool walled(World const& w, int axis, int line) {
    int const size = axis == 0 ? w.W : w.H, across = axis == 0 ? w.H : w.W;
    int const at = line == 0 ? size / 2 : 0;
    int kelp = 0;
    for (int i = 0; i < across; i++) {
        int const t = axis == 0 ? w.tileOf(at, i) : w.tileOf(i, at);
        std::uint8_t const kind = w.edgeKind[w.edgeOf(t, axis == 0 ? 3 : 0)];
        if (kind == EDGE_OPEN)
            return false;
        kelp += kind == EDGE_KELP;
    }
    return kelp >= P.raceWallSeen;
}

// The live symmetry candidates as World::symLive's bits: the known one alone, else every one that fits what we've seen,
// else (every candidate contradicted: we know nothing) all three.
inline int liveCandidates(World const& w) {
    int const live = w.symmetry >= 0 ? 1 << w.symmetry : w.symLive;
    return live == 0 ? 7 : live;
}

// Whether a live symmetry candidate flips the axis (0 x, 1 y): candidate 0 flips y, 1 flips x, 2 both (World::symLive).
inline bool mirrored(World const& w, int axis) {
    int const live = liveCandidates(w);
    return (live >> 2 & 1) || (live >> (axis == 0 ? 1 : 0) & 1);
}

// The chosen coordinate on one axis (see the top of this file), -1 when both lines are walled.
inline int axisTarget(World const& w, int axis) {
    int const size = axis == 0 ? w.W : w.H;
    int const born = axis == 0 ? w.xOf(w.bornHead) : w.yOf(w.bornHead);
    if (!mirrored(w, axis))
        return born;
    int best = -1;
    double bestDist = -1;
    for (int line = 0; line < 2; line++) {
        double const d = axisDist(born, linePos(size, line), size);
        if (d > bestDist && !walled(w, axis, line)) {
            bestDist = d;
            best = lineCoord(size, line, born);
        }
    }
    return best;
}

} // namespace race_detail

// Steps from our birth tile over known edges, unknown ones open except across a mirror line of a mirrored axis that
// race_detail::walled judges walled (the race's own reading of it: the halves meet the long way round there). A portal whose far end we haven't
// seen may land anywhere, so no tile counts as farther than one step past the nearest such portal. By the map's
// symmetry, at(mirror of t) is how far our twin, the enemy starter born on our birth tile's mirror, can be from t
// (raceDefers).
struct BirthField {
    std::vector<int> dist, queue;
    int portalCap = DistField::FAR; // one step past the nearest unknown portal, FAR with none reached

    int at(int t) const { return std::min(dist[t] < 0 ? DistField::FAR : dist[t], portalCap); }

    void build(World const& w) {
        dist.assign(w.N, -1);
        queue.resize(w.N);
        bool wall[2][2];
        for (int axis = 0; axis < 2; axis++)
            for (int line = 0; line < 2; line++)
                wall[axis][line] = race_detail::mirrored(w, axis) && race_detail::walled(w, axis, line);
        int const midX = w.W / 2, midY = w.H / 2;
        // Whether edge e is an unknown crossing of a walled line: a west side in the line's column (x) or a north side
        // in its row (y), as race_detail::walled reads them.
        auto const shut = [&](int e) {
            if (w.edgeKind[e] != EDGE_UNKNOWN)
                return false;
            if (e >= w.N) {
                int const x = w.xOf(e - w.N);
                return (x == midX && wall[0][0]) || (x == 0 && wall[0][1]);
            }
            int const y = w.yOf(e);
            return (y == midY && wall[1][0]) || (y == 0 && wall[1][1]);
        };
        std::size_t tail = 0;
        dist[w.bornHead] = 0;
        queue[tail++] = w.bornHead;
        portalCap = DistField::FAR;
        for (std::size_t at = 0; at < tail; at++) {
            int const cur = queue[at];
            for (int dir = 0; dir < 4; dir++) {
                int const to = w.step(cur, dir);
                if (to == UNKNOWN_PORTAL)
                    portalCap = std::min(portalCap, dist[cur] + 1);
                if (to < 0 || dist[to] >= 0 || shut(w.edgeOf(cur, dir)))
                    continue;
                dist[to] = dist[cur] + 1;
                queue[tail++] = to;
            }
        }
    }
};

// Whether tile t is on the band (contested ground both ways, contested.hpp's band()) under some live symmetry
// candidate, read from the birth field: our birth tile and our twin reach t within P.contestedSlack steps of each other,
// both by a known path (a distance capped at an unknown portal says nothing about which side is nearer).
inline bool onBand(World const& w, BirthField const& f, int t) {
    auto const real = [&](int u) { return f.dist[u] >= 0 && f.dist[u] <= f.portalCap; };
    if (!real(t))
        return false;
    int const live = race_detail::liveCandidates(w);
    for (int k = 0; k < 3; k++) {
        int const m = w.mirrorTile(t, k);
        if ((live >> k & 1) && real(m) && std::abs(f.dist[m] - f.dist[t]) <= P.contestedSlack)
            return true;
    }
    return false;
}

// Heads on the band this turn (I1126), for raceDefers' commit: `ours`, a, counts this dragon (racing there), teammates
// in view with their head on the band and the claimants of fresh pearl claims on band tiles (contested.hpp's count, on
// the birth field's band); `theirs`, b, counts enemy heads in view on the band, plus one for each block of the team
// belief (belief.hpp) none of it in our view, whose last report within P.enemyHalfLife rounds saw a band tile and had
// an enemy head (a block's report carries one bit, so a block counts one head). Until the band is `observed` (a band
// tile in view, or such a report on one) b is a: by the mirror their starters race there as ours do.
struct BandHeads {
    int ours = 1, theirs = 1;
    bool observed = false;
    std::vector<int> ids; // scratch: claimant ids counted

    void count(World const& w, BirthField const& f, Belief const* team, Claims const* claims) {
        ids.assign(1, w.myId & PearlClaim::ID_MASK);
        theirs = 0;
        observed = false;
        for (auto const& d : w.dragons) {
            if (d.id == w.myId || d.head < 0 || !onBand(w, f, d.head))
                continue;
            if (d.enemy)
                theirs++;
            else
                ids.push_back(d.id & PearlClaim::ID_MASK);
        }
        if (claims && P.pearlClaims && !claims->eat.empty())
            for (int t = 0; t < w.N; t++)
                if (claims->eat[t] >= w.round && std::find(ids.begin(), ids.end(), claims->by[t]) == ids.end() &&
                    onBand(w, f, t))
                    ids.push_back(claims->by[t]);
        ours = static_cast<int>(ids.size());
        for (int t = 0; t < w.N && !observed; t++)
            observed = w.visible(t) && onBand(w, f, t);
        if (team && !team->enemyRound.empty()) {
            for (int b = 0; b < team->blocks(); b++) {
                if (team->enemyRound[b] < 0 || w.round - team->enemyRound[b] > P.enemyHalfLife)
                    continue;
                bool band = false, inView = false;
                for (int i = 0; i < BLOCK * BLOCK && !inView; i++) {
                    int const t = team->cellTile(b, i);
                    if (t < 0)
                        continue;
                    inView = w.visible(t);
                    band = band || (team->seen[t] >= team->enemyRound[b] && onBand(w, f, t));
                }
                if (!band || inView)
                    continue;
                observed = true;
                theirs += team->enemyThere[b];
            }
        }
        if (!observed)
            theirs = ours;
    }
};

// Whether the race passes up pearl target t, `myDist` path steps from our head (Targets::choosePearl; `raceField` from
// the race target over known edges with unknown ones open, `bornField` from our birth tile). With P.bandCommit (I1118)
// it does for home food (a pearl, fast tile or drop), on no contested ground under any live symmetry candidate: the
// enemy starter born on our birth tile's mirror (our twin) needs more than P.contestedSlack steps more than our birth
// tile to reach it (contested.hpp's rule, read before the symmetry settles). Home food waits for us, while what we leave
// between the homes goes to them, so the round-0 dragons walk to the ground between the homes together instead of
// eating their way there one by one; by the mirror each reaches it as soon as its twin does. Kept all the same: food on
// a shortest path to the target (it costs the race no step), food some visible enemy head is as near as we are (the
// twin's distance says nothing when an enemy is already there) and, while one more pearl brings us to P.splitAtLength,
// any food: it adds a head to the ones we bring (Default Small's 3-long starters had passed up pearls 2 steps off and
// reached the band unsplit, against heads that had split). Online, enemies ate 94 of the pearls on our home ground
// in r0-59 of 60 Devil games against our 1706, while eating twice our pearls on the ground between the homes (E0352).
// With P.bandContested (I1126) it passes up home food only while the band is contested (`fight`: BandHeads' b >= a,
// the enemy heads we see or hear there at least ours, or the band not yet seen or heard of), and races as without
// P.bandCommit once the band is seen holding fewer enemy heads than ours: E0412's commit
// gained where the opponent masses on the band (near_devil 0.594 over 69 v rusher) and lost where it doesn't
// (near_default_small 0.421 over 54).
inline bool raceDefers(World const& w, DistField const& raceField, BirthField const& bornField, int race, int t,
                       int myDist, bool fight = true) {
    if (!P.bandCommit || !fight || race < 0 || w.length + 1 >= P.splitAtLength)
        return false;
    if (raceField.at(t) < DistField::FAR && myDist + raceField.at(t) <= raceField.at(w.head))
        return false;
    int const live = race_detail::liveCandidates(w);
    for (int k = 0; k < 3; k++)
        if ((live >> k & 1) && bornField.at(w.mirrorTile(t, k)) <= bornField.at(t) + P.contestedSlack)
            return false;
    for (auto const& d : w.dragons)
        if (d.enemy && d.head >= 0 && w.torusDist(d.head, t) <= myDist)
            return false;
    return true;
}

// This turn's race target (see the top of this file), with `field` built from it out to W + H steps; -1 when the race
// is off, over (past P.raceRounds, or the target seen since our birth) or the target is out of reach. With
// P.raceByReach (I707) the race runs on any map where it arrives in time: `arrive`, kept by the caller (-1 until set),
// is the round we would reach the target, our round plus our path to it on the first turn we have one, and a dragon
// that would get there only after P.raceRounds doesn't race at all. By the mirror our twin is no nearer the target, so
// who gets there first is settled; whether we get there while the race lasts is what the map's size stood for.
// Without it, only maps of at most P.raceMaxArea tiles race.
inline int raceTarget(World const& w, DistField& field, int& arrive) {
    using namespace race_detail;
    bool const late = P.raceByReach ? arrive > P.raceRounds : w.N > P.raceMaxArea;
    if (!P.openingRace || w.round >= P.raceRounds || late || w.bornHead < 0 || w.firstRound != 0)
        return -1;
    int const x = axisTarget(w, 0), y = axisTarget(w, 1);
    if (x < 0 || y < 0)
        return -1;
    int const target = w.tileOf(x, y);
    if (w.lastSeen[target] >= w.firstRound)
        return -1;
    field.build(w, target, w.W + w.H, w.head);
    int const path = field.at(w.head);
    if (path >= DistField::FAR)
        return -1;
    if (arrive < 0)
        arrive = w.round + path;
    return P.raceByReach && arrive > P.raceRounds ? -1 : target;
}

} // namespace bot
