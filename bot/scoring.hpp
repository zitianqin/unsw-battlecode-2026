#pragma once

#include "doorway.hpp"
#include "farm.hpp"
#include "jam.hpp"
#include "notes.hpp"
#include "rammodel.hpp"
#include "safety.hpp"
#include "seal.hpp"
#include "search.hpp"
#include "state.hpp"
#include "targets.hpp"

#include <algorithm>
#include <array>
#include <cmath>

namespace bot {

// The parts a move's score is made of, named for the debug log line (decidingTerm): the pearl or explore target's
// distance, the space share, repulsion from teammates' heads, eating a pearl, stepping onto a spawn before it is due,
// and every survival penalty (shortfall, risk, a teammate's exit, a head-reach cut-off, the price of crossing an
// unknown portal) or fixed score (kelp, a head-on, an unknown portal we don't cross, a lethal body), and the
// tie-break for ending on a portal doorway (doorway.hpp).
enum Term {
    TERM_TARGET,
    TERM_EXPLORE,
    TERM_SPACE,
    TERM_REPEL,
    TERM_PEARL,
    TERM_SPAWN,
    TERM_PENALTY,
    TERM_DOORWAY,
    TERM_COUNT
};
inline constexpr char const* TERM_NAME[TERM_COUNT] = {"target", "explore", "space",   "repel",
                                                      "pearl",  "spawn",   "penalty", "doorway"};

// One of the four single-step moves, with what we worked out about it.
struct MoveEval {
    int dir = 0;
    int dest = -1;
    double score = 0;
    int space = 0;       // tiles we reach before every other head (capped): our share, a soft preference
    int room = 0;        // tiles walls and timed bodies let us reach (capped), whoever gets there first
    bool closed = false; // the physical region after this move is a closed pocket (see Safety::floodFill)
    bool farm = false;   // closed, but farmEntryOk accepted it, so it carries no shortfall (farm.hpp)
    bool safe = false;   // an ordinary step with no survival penalty: not lethal, not risky, no shortfall,
                         // not a visible teammate's only exit, not sealing a teammate in, not cut off and not
                         // lost sooner in the local search (evaluateMoves)
    bool open = false;   // not closed and room enough: no shortfall
    bool cutOff = false; // open, but another head can seal it off before our next move (jam.hpp)
    bool step = false;   // a step onto a tile that is free by then (not kelp, a portal we don't know, a head or a
                         // body still there): the moves sealsTeammate compares
    bool friendExit = false; // it takes a visible teammate's only exit (Safety::blocksTeammateExit)
    bool survives = false;   // a step with no survival penalty (shortfall, ram risk, a teammate's only exit), unlike
                             // `safe` indifferent to the out-of-sight tie-break: the steps that keep a teammate's way
                             // out (sealsTeammate)
    int lostAt = 0;      // open, but the enemy heads near us can force our loss this many moves in (search.hpp)
    std::array<double, TERM_COUNT> terms{}; // score's signed parts by Term, for the log only: score is summed
                                            // on its own, in its own order
};

// Scores one single-step move. Survival dominates through the large constants in Params (lethal, bad
// trade, closed pocket or one too small, enemy could ram us, a visible teammate's only exit); pearls, targets and spacing only order the
// safe moves. Pockets are judged physically: open ground another head merely reaches first is contested,
// not a dead end, so it only lowers the soft space term. A closed pocket worth farming (farm.hpp) is not
// penalised at all: we go in, eat, and split our rear back out; unless `mayFarm` is false (a king that holds its
// splits, fold.hpp's kingSkipsFarms), which pays the shortfall for every closed pocket. A crossing a teammate's
// doorway probe says lands on its head (World::doorProbed, doorway.hpp) scores as a head-on with it; `heedDoor` false
// scores it as if no probe came (the debug line's doorflip).
inline MoveEval evaluateMove(World const& w, Safety& safety, Targets const& targets, int dir, bool mayFarm = true,
                             bool heedDoor = true) {
    MoveEval m;
    m.dir = dir;
    int const dest = w.step(w.head, dir);
    m.dest = dest;
    auto& pens = notes.movePens[dir];
    // Names the fixed score a move that is no step returns with (Notes::moveFixed).
    auto const fixed = [dir](char const* kind) {
        if constexpr (kNotes)
            notes.moveFixed[dir] = kind;
    };
    if constexpr (kNotes)
        pens = {};
    fixed(nullptr);
    if (heedDoor && w.doorProbed[dir]) {
        if constexpr (kNotes) {
            notes.doorUnprobed[dir] = evaluateMove(w, safety, targets, dir, mayFarm, false).score;
            pens = {};
        }
        fixed("door");
        m.score = m.terms[TERM_PENALTY] = P.friendHeadOnScore;
        return m;
    }
    if (dest == KELP) {
        fixed("lethal");
        m.score = m.terms[TERM_PENALTY] = P.lethalScore;
        return m;
    }
    if (dest == UNKNOWN_PORTAL) {
        // A portal is a door, not a wall: when it is where our explore target sends us, crossing scores like
        // an open move that reaches the target, less a small price for landing blind.
        if (targets.crossesExplorePortal(w, dir)) {
            m.score = P.spaceWeight * P.spaceCap - P.portalCrossPenalty;
            m.terms[TERM_SPACE] = P.spaceWeight * P.spaceCap;
            m.terms[TERM_PENALTY] = -P.portalCrossPenalty;
        } else {
            m.score = m.terms[TERM_PENALTY] = -P.unknownPortalPenalty;
        }
        fixed("portal");
        return m;
    }

    if (w.occId[dest] >= 0 && w.occHead[dest] && w.occId[dest] != w.myId) {
        if (!w.occEnemy[dest]) {
            fixed("headon");
            m.score = m.terms[TERM_PENALTY] = P.friendHeadOnScore;
            return m;
        }
        fixed("ram");
        SeenDragon const* them = w.dragonAt(dest);
        int const theirLen = them ? them->visibleLen : 2;
        if (ramWorth(w, safety, dest, theirLen, {dir}))
            m.score = P.attackBonus + P.attackPerLength * (theirLen - w.length);
        else
            m.score = P.badTradeScore + theirLen;
        m.terms[TERM_PENALTY] = m.score;
        return m;
    }
    if (w.freeAt(dest, 0) > 1) {
        fixed("lethal");
        m.score = m.terms[TERM_PENALTY] = P.lethalScore;
        return m;
    }

    m.step = true;
    bool const eats = w.hasPearlHere(dest);
    int const cap = fillCap(w), hold = holdCap(w);
    int const need = std::min(w.length + (eats ? 1 : 0) + P.spaceMargin, hold);
    m.space = safety.floodFill(dest, eats ? 1 : 0, cap);
    m.room = m.space;
    bool freed = safety.reachedFreed;
    if (m.room < cap) {
        m.room = safety.floodFill(dest, eats ? 1 : 0, cap, {.physical = true});
        freed = safety.reachedFreed;
    }
    if constexpr (kNotes) {
        notes.moveFill[dir] = notes.fill;
        notes.moveFill[dir].ran = true;
    }
    m.closed = m.room < hold && !freed;
    double score = 0;
    if (m.closed) {
        m.farm = mayFarm && farmEntryOk(w, safety, dest, eats ? 1 : 0);
        if (!m.farm)
            score -= P.shortfallPerTile * (hold - m.room);
    } else if (m.room < need)
        score -= P.shortfallPerTile * (need - m.room);
    if constexpr (kNotes)
        pens[PEN_SHORT] = score;
    m.open = !m.closed && m.room >= need;
    if (m.open)
        m.cutOff = cutOffByHeads(w, safety, dest, eats ? 1 : 0, hold, need);
    int const seenNeed = std::min(need, P.visibleNeedCap);
    if (m.room >= need && seenNeed > 1) {
        int const seenSpace =
            safety.floodFill(dest, eats ? 1 : 0, seenNeed, {.visibleOnly = true, .physical = true});
        if (seenSpace < seenNeed) {
            score -= P.unseenShortfallPerTile * (seenNeed - seenSpace);
            if constexpr (kNotes)
                pens[PEN_UNSEEN] = -P.unseenShortfallPerTile * (seenNeed - seenSpace);
        }
    }
    // P.harvestStep: onto a drop pearl beside no enemy head longer than us, where a ram on us is at worst even: not
    // risky, for the penalty and for m.survives.
    bool const harvest = P.harvestStep && w.dropNow(dest) && !safety.longerEnemyNext(dest);
    bool const risky = !harvest && safety.riskyFor(dest);
    if (risky)
        score -= P.riskPenalty;
    m.friendExit = safety.blocksTeammateExit(dest, eats);
    if (m.friendExit)
        score -= P.friendExitPenalty;
    if constexpr (kNotes) {
        pens[PEN_RISK] = risky ? -P.riskPenalty : 0;
        pens[PEN_FRIEND] = m.friendExit ? -P.friendExitPenalty : 0;
    }
    m.survives = (m.closed ? m.farm : m.room >= need) && !risky && !m.friendExit;
    m.safe = score == 0;
    m.terms[TERM_PENALTY] = score;
    m.terms[TERM_SPACE] = P.spaceWeight * std::min(m.space, P.spaceCap);
    score += P.spaceWeight * std::min(m.space, P.spaceCap);
    if (eats) {
        m.terms[TERM_PEARL] = P.pearlBonus;
        score += P.pearlBonus;
    }

    if (targets.pearl >= 0) {
        m.terms[TERM_TARGET] = -P.targetWeight * std::min(targets.pearlField.at(dest), 1000);
        score -= P.targetWeight * std::min(targets.pearlField.at(dest), 1000);
        if (dest == targets.pearl && targets.pearlReady > 1) {
            m.terms[TERM_SPAWN] = -P.earlySpawnPenalty;
            score -= P.earlySpawnPenalty;
        }
    } else if (targets.explore >= 0) {
        m.terms[TERM_EXPLORE] = -P.exploreWeight * std::min(targets.exploreField.at(dest), 1000);
        score -= P.exploreWeight * std::min(targets.exploreField.at(dest), 1000);
    }

    for (auto const& d : w.dragons) {
        if (d.enemy || d.id == w.myId)
            continue;
        int const close = P.friendRepelRadius - w.torusDist(dest, d.head);
        if (close > 0) {
            m.terms[TERM_REPEL] -= P.friendRepelWeight * close;
            score -= P.friendRepelWeight * close;
        }
    }
    m.score = score;
    return m;
}

// The doorway charge (doorway.hpp) for each of four scored moves, indexed by direction: a move's doorwayCharge()
// applies when some other move with no charge of its own and no survival penalty beyond the out-of-sight tie-break
// (the P.unseenShortfallPerTile term) ends at least as near the target by its path field (with no target, any such
// move) and already scores at least as well as every move ending farther from it (or off the board: kelp, an unknown
// portal), so the charge can hand the turn only to a move of equal progress, never to a step back; else 0.
inline std::array<double, 4> doorwayCharges(World const& w, Targets const& targets,
                                            std::array<MoveEval, 4> const& moves) {
    std::array<double, 4> charge{};
    for (auto const& m : moves)
        charge[m.dir] = doorwayCharge(w, targets, m.dir, m.dest);
    DistField const* field = progressField(targets);
    auto const progress = [&](int dest) { return field ? field->at(dest) : 0; };
    std::array<double, 4> applied{};
    for (auto const& m : moves) {
        if (charge[m.dir] == 0)
            continue;
        for (auto const& alt : moves) {
            if (alt.dir == m.dir || alt.dest < 0 || charge[alt.dir] > 0 ||
                alt.terms[TERM_PENALTY] < -P.unseenShortfallPerTile * P.visibleNeedCap ||
                progress(alt.dest) > progress(m.dest))
                continue;
            bool leads = true;
            for (auto const& back : moves)
                if (back.dir != m.dir && (back.dest < 0 || progress(back.dest) > progress(m.dest)))
                    leads = leads && alt.score >= back.score;
            if (leads) {
                applied[m.dir] = charge[m.dir];
                break;
            }
        }
    }
    return applied;
}

// All four moves, indexed by direction. An open move another head can cut off (MoveEval::cutOff) costs
// P.headReachPenalty, but only while some other open move can't be cut off: when every open move can, the scores
// stand as they are, since a check that closes every move tells them apart no better than none. Likewise the local
// search (search.hpp) ranks the open moves by their worst case: an open move the enemy heads near us can force lost
// sooner than the best open move above P.doomedScore costs P.searchLossPenalty per move sooner. The moves it doesn't
// search (closed pockets, farms) keep their scores, and when every open move is lost as soon, the scores stand. A step that squeezes a
// visible teammate in while another step with no survival penalty keeps its way out (seal.hpp's squeeze guard) costs
// P.squeezePenalty when squeezeCharged says so, unless it already paid P.friendExitPenalty for taking the teammate's only exit. With P.keepDoorways a move pays its doorwayCharges()
// last, once every survival penalty is in. `mayFarm` as for evaluateMove.
inline std::array<MoveEval, 4> evaluateMoves(World const& w, Safety& safety, Targets const& targets,
                                             bool mayFarm = true) {
    std::array<MoveEval, 4> moves;
    bool uncut = false;
    std::array<bool, 4> steps{}, survive{};
    for (int dir = 0; dir < 4; dir++) {
        moves[dir] = evaluateMove(w, safety, targets, dir, mayFarm);
        uncut = uncut || (moves[dir].open && !moves[dir].cutOff);
        steps[dir] = moves[dir].step;
        survive[dir] = moves[dir].survives;
    }
    std::array<int, 4> const seals = sealsTeammate(w, safety, steps, survive);
    for (auto& m : moves) {
        if (!m.friendExit && squeezeCharged(seals[m.dir], m.step && w.hasPearlHere(m.dest))) {
            m.score -= P.squeezePenalty;
            m.terms[TERM_PENALTY] -= P.squeezePenalty;
            m.safe = false;
            if constexpr (kNotes)
                notes.movePens[m.dir][PEN_SEAL] = -P.squeezePenalty;
        }
    }
    if (uncut) {
        for (auto& m : moves) {
            if (m.cutOff) {
                m.score -= P.headReachPenalty;
                m.terms[TERM_PENALTY] -= P.headReachPenalty;
                m.safe = false;
                if constexpr (kNotes)
                    notes.movePens[m.dir][PEN_CUT] = -P.headReachPenalty;
            }
        }
    }
    SearchVerdict const search = searchMoves(w, safety, {moves[0].open, moves[1].open, moves[2].open, moves[3].open});
    auto const lasts = [&](MoveEval const& m) { return m.lostAt ? m.lostAt - 1 : search.depth; };
    int best = 0;
    for (auto& m : moves) {
        m.lostAt = search.lostAt[m.dir];
        if (m.open && m.score > P.doomedScore)
            best = std::max(best, lasts(m));
    }
    for (auto& m : moves) {
        if (m.lostAt && lasts(m) < best) {
            double const penalty = P.searchLossPenalty * (best - lasts(m));
            m.score -= penalty;
            m.terms[TERM_PENALTY] -= penalty;
            m.safe = false;
            if constexpr (kNotes)
                notes.movePens[m.dir][PEN_SEARCH] = -penalty;
        }
    }
    if (P.keepDoorways) {
        std::array<double, 4> const doorway = doorwayCharges(w, targets, moves);
        for (auto& m : moves) {
            m.score -= doorway[m.dir];
            m.terms[TERM_DOORWAY] = -doorway[m.dir];
        }
    }
    return moves;
}

// The best of evaluated moves. With P.moveKeepHeading a tie that includes `heading` (our facing) goes straight
// on; other ties go to the first in N, E, S, W order.
inline MoveEval bestOf(std::array<MoveEval, 4> const& moves, int heading = -1) {
    MoveEval best;
    best.score = P.lethalScore * 10;
    for (auto const& m : moves)
        if (m.score > best.score || (P.moveKeepHeading && m.score == best.score && m.dir == heading))
            best = m;
    return best;
}

// The best-scored of the moves other than `chosen` (a direction), the first in N, E, S, W order on a tie: what the
// chosen move beat, for the debug log line.
inline MoveEval const& runnerUp(std::array<MoveEval, 4> const& moves, int chosen) {
    int alt = chosen == 0 ? 1 : 0;
    for (int dir = alt + 1; dir < 4; dir++)
        if (dir != chosen && moves[dir].score > moves[alt].score)
            alt = dir;
    return moves[alt];
}

// The name (TERM_NAME) of the score part that differs most between `best` and `alt`, the first in Term order on a
// tie, or "none" when every part is equal. With `favouring`, only parts higher on `best` count: what made `best`
// beat `alt`, rather than the widest gap either way.
inline char const* decidingTerm(MoveEval const& best, MoveEval const& alt, bool favouring = false) {
    int term = -1;
    double most = 0;
    for (int t = 0; t < TERM_COUNT; t++) {
        double const gap = best.terms[t] - alt.terms[t];
        double const diff = favouring ? gap : std::abs(gap);
        if (diff > most) {
            most = diff;
            term = t;
        }
    }
    return term < 0 ? "none" : TERM_NAME[term];
}

// The name (PEN_NAME) of the penalty part that differs most between `best` and `alt` (with `favouring`, only parts
// higher on `best`, as decidingTerm), the first in Pen order on a tie, or "none" when none differs; a fixed part is
// named by its kind (lethal, door, portal, headon or ram: Notes::moveFixed), the lower one's when both moves have one,
// "fixed" when neither does. Reads notes.movePens (this turn's evaluateMoves), so only with kNotes.
inline char const* decidingPenalty(MoveEval const& best, MoveEval const& alt, bool favouring = false) {
    // The fixed part is what the others leave of terms[TERM_PENALTY] (all of it on a move that is no step).
    auto const part = [](MoveEval const& m, int p) {
        auto const& pens = notes.movePens[m.dir];
        if (p != PEN_FIXED)
            return pens[p];
        double rest = m.terms[TERM_PENALTY];
        for (int q = PEN_FIXED + 1; q < PEN_COUNT; q++)
            rest -= pens[q];
        return rest;
    };
    int mostPart = -1;
    double most = 0;
    for (int p = 0; p < PEN_COUNT; p++) {
        double const gap = part(best, p) - part(alt, p);
        double const diff = favouring ? gap : std::abs(gap);
        if (diff > most) {
            most = diff;
            mostPart = p;
        }
    }
    if (mostPart == PEN_FIXED) {
        char const* const kinds[2] = {notes.moveFixed[best.dir], notes.moveFixed[alt.dir]};
        bool const bestLower = part(best, PEN_FIXED) < part(alt, PEN_FIXED);
        if (char const* kind = kinds[bestLower ? 0 : 1] ? kinds[bestLower ? 0 : 1] : kinds[bestLower ? 1 : 0])
            return kind;
    }
    return mostPart < 0 ? "none" : PEN_NAME[mostPart];
}

inline MoveEval bestMove(World const& w, Safety& safety, Targets const& targets) {
    return bestOf(evaluateMoves(w, safety, targets), w.facing[w.head]);
}

} // namespace bot
