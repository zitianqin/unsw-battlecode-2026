#pragma once

#include "belief.hpp"
#include "params.hpp"
#include "state.hpp"

#include <algorithm>
#include <bit>
#include <utility>

namespace bot {

// The map's symmetry. Every map is its own mirror image under one of three candidates (World::symLive): a
// flip of y, a flip of x, or both (a 180-degree rotation) (docs map-info.md). Mirrored tiles share one pearl
// countdown (pearls.md) and portals map onto portals. Each candidate is dropped the first time a tile and its
// image under it, both seen (by us, or a teammate's countdown in the belief map), disagree on an edge (open,
// kelp or portal) or a countdown. Once one candidate is left (or a teammate reports one still consistent
// with our sightings), every tile we have seen also writes its image's
// edges and countdown memory (flagged mirrored, so a later sighting overwrites it), so paths, pearl targets,
// fast tiles and the unknown-portal rules see the mirror half too. If that candidate is contradicted in turn,
// every mirrored write is dropped. Call readSymmetry() each turn after Belief::hear().

// Whether two readings of countdowns, spawning at round sa read at ra and at sb read at rb, can be one
// countdown's: a countdown read at r is valid until its spawn round, when it resets to a fresh draw.
inline bool sameCountdown(int ra, int sa, int rb, int sb) {
    if (ra > rb) {
        std::swap(ra, rb);
        std::swap(sa, sb);
    }
    return rb >= sa || sa == sb;
}

namespace symmetry_detail {

inline bool seenEdge(World const& w, int e) { return w.edgeKind[e] != EDGE_UNKNOWN && !w.edgeMirrored[e]; }
inline bool seenTile(World const& w, int t) { return w.spawnKind[t] != SPAWN_UNKNOWN && !w.tileMirrored[t]; }

// Whether tile t (seen) and its image m under candidate k can be mirror images, on our own memory. A tile on
// the mirror line is its own image, but its sides still pair up (east with west under a flip of x).
inline bool tileFits(World const& w, int t, int k) {
    int const m = w.mirrorTile(t, k);
    for (int d = 0; d < 4; d++) {
        int const e = w.edgeOf(t, d), me = w.edgeOf(m, World::mirrorDir(d, k));
        if (seenEdge(w, e) && seenEdge(w, me) && w.edgeKind[e] != w.edgeKind[me])
            return false;
    }
    if (!seenTile(w, t) || !seenTile(w, m))
        return true;
    if (w.spawnKind[t] != w.spawnKind[m])
        return false;
    return w.spawnKind[t] != SPAWN_YES || sameCountdown(w.spawnRead[t], w.spawnRound[t], w.spawnRead[m], w.spawnRound[m]);
}

// Whether a countdown the team read at tile t fits its image m under candidate k: the team's countdown there,
// or our own memory of m.
inline bool teamCountdownFits(World const& w, Belief const& b, int t, int k) {
    int const m = w.mirrorTile(t, k);
    if (m == t)
        return true;
    if (seenTile(w, m) && w.spawnKind[m] == SPAWN_NEVER)
        return false;
    return b.spawnSeen[m] < 0 || sameCountdown(b.spawnSeen[t], b.spawn[t], b.spawnSeen[m], b.spawn[m]);
}

// Marks edge e as a mirrored write of `kind` (portal id `id`) unless it has been seen.
inline void writeEdge(World& w, int e, EdgeKind kind, int id = -1) {
    if (seenEdge(w, e))
        return;
    w.setEdge(e, kind, id);
    w.edgeMirrored[e] = 1;
}

// Writes the mirror of seen portal edge x and of its pair under candidate k. The image of x is a portal whose
// partner is the image of x's partner; it takes the id of whichever image of the pair has been seen, else a
// made-up negative one (-2 - x's id) shared by both images. A pair seen from one end only takes its partner
// from its image's pair when that is known.
inline void writePortal(World& w, int x, int k) {
    int const mx = w.mirrorEdge(x, k);
    if (mx == x)
        return;
    int const px = w.portalPartner(x);
    int const mpx = px >= 0 ? w.mirrorEdge(px, k) : -1;
    int id = -2 - w.edgePortal[x];
    if (seenEdge(w, mx))
        id = w.edgePortal[mx];
    else if (mpx >= 0 && seenEdge(w, mpx) && w.edgeKind[mpx] == EDGE_PORTAL)
        id = w.edgePortal[mpx];
    if (w.edgeKind[mx] != EDGE_PORTAL && seenEdge(w, mx))
        return; // a contradiction, caught by the next check
    writeEdge(w, mx, EDGE_PORTAL, id);
    if (mpx >= 0) {
        writeEdge(w, mpx, EDGE_PORTAL, id);
    } else if (int const pmx = w.portalPartner(mx); pmx >= 0) {
        writeEdge(w, w.mirrorEdge(pmx, k), EDGE_PORTAL, w.edgePortal[x]);
    }
}

// Writes what we have seen of tile t (its edges and countdown memory) into its image under candidate k.
inline void writeTile(World& w, int t, int k) {
    int const m = w.mirrorTile(t, k);
    if (P.mirrorEdges) {
        for (int d = 0; d < 4; d++) {
            int const e = w.edgeOf(t, d);
            if (!seenEdge(w, e))
                continue;
            if (w.edgeKind[e] == EDGE_PORTAL)
                writePortal(w, e, k);
            else
                writeEdge(w, w.edgeOf(m, World::mirrorDir(d, k)), static_cast<EdgeKind>(w.edgeKind[e]));
        }
    }
    if (!P.mirrorCountdowns || !seenTile(w, t) || seenTile(w, m))
        return;
    // A mirrored countdown that fired unseen means a probable pearl (targets.hpp): t's next draw doesn't replace
    // it until that goes stale.
    bool const probable = w.spawnKind[m] == SPAWN_YES && w.spawnRound[m] <= w.spawnRead[t] &&
                          w.lastSeen[m] < w.spawnRound[m] && w.round - w.spawnRound[m] <= P.pearlMemoryRounds;
    w.tileMirrored[m] = 1;
    if (!probable) {
        w.spawnKind[m] = w.spawnKind[t];
        w.spawnRound[m] = w.spawnRound[t];
        w.spawnRead[m] = w.spawnRead[t];
    }
    if (w.resetCount[t] > w.resetCount[m]) {
        bool const listed = w.resetCount[m] >= P.fastMinResets;
        w.resetCount[m] = w.resetCount[t];
        w.resetSum[m] = w.resetSum[t];
        if (!listed && w.resetCount[m] >= P.fastMinResets)
            w.resetTiles.push_back(m);
    }
}

// Forgets every mirrored write: the candidate they came from was contradicted.
inline void dropMirrors(World& w) {
    for (int e = 0; e < 2 * w.N; e++) {
        if (w.edgeMirrored[e]) {
            w.edgeMirrored[e] = 0;
            w.setEdge(e, EDGE_UNKNOWN);
        }
    }
    for (int t = 0; t < w.N; t++) {
        if (!w.tileMirrored[t])
            continue;
        w.tileMirrored[t] = 0;
        w.spawnKind[t] = SPAWN_UNKNOWN;
        w.spawnRound[t] = -1;
        w.spawnRead[t] = -1;
        w.resetCount[t] = 0;
        w.resetSum[t] = 0;
    }
    std::erase_if(w.resetTiles, [&](int t) { return w.resetCount[t] < P.fastMinResets; });
}

} // namespace symmetry_detail

// This turn's symmetry step (see the top of this file): tiles in view are seen now, so their mirrored flags
// clear; every live candidate is checked against them and against the team's countdowns; then mirrored
// writes follow the one candidate left, or a shared candidate that still fits. A claim never discards
// other candidates: contradicting it leaves our own evidence available to find the true mirror.
inline void readSymmetry(World& w, Belief const& b) {
    using namespace symmetry_detail;
    if (!P.useSymmetry)
        return;
    for (int t : w.touched) {
        if (!w.visible(t))
            continue;
        w.tileMirrored[t] = 0;
        for (int d = 0; d < 4; d++)
            w.edgeMirrored[w.edgeOf(t, d)] = 0;
    }
    for (int k = 0; k < 3; k++) {
        if (!(w.symLive >> k & 1))
            continue;
        bool fits = true;
        for (int t : w.touched)
            fits = fits && (!w.visible(t) || tileFits(w, t, k));
        for (int t : b.recentSpawns)
            fits = fits && teamCountdownFits(w, b, t, k);
        if (!fits)
            w.symLive &= ~(1 << k);
    }
    int one = std::popcount(static_cast<unsigned>(w.symLive)) == 1 ? std::countr_zero(static_cast<unsigned>(w.symLive)) : -1;
    if (one < 0 && P.shareSymmetry && b.sharedSymmetry >= 0 && (w.symLive >> b.sharedSymmetry & 1))
        one = b.sharedSymmetry;
    if (one != w.symmetry) {
        if (w.symmetry >= 0)
            dropMirrors(w);
        w.symmetry = one;
        if (one >= 0)
            for (int t = 0; t < w.N; t++)
                writeTile(w, t, one);
    } else if (one >= 0) {
        for (int t : w.touched)
            if (w.visible(t))
                writeTile(w, t, one);
    }
}

} // namespace bot
