#include "support.hpp"
#include "scoring.hpp"

using test::require;

namespace {

constexpr int N = 0, E = 1, S = 2, W = 3;

// Walls every edge of the tiles `inside` accepts that leads to a tile it doesn't.
template <typename Inside> void enclose(bot::World& w, Inside inside) {
    for (int y = 0; y < w.H; y++)
        for (int x = 0; x < w.W; x++)
            if (inside(x, y))
                for (int dir = 0; dir < 4; dir++)
                    if (!inside((x + bot::DX[dir] + w.W) % w.W, (y + bot::DY[dir] + w.H) % w.H))
                        test::wall(w, w.tileOf(x, y), dir);
}

// A visible teammate's body, head first, with the tail distances a seen tail gives.
void teammate(bot::World& w, int id, std::vector<int> const& tiles) {
    test::dragon(w, id, false, tiles.front(), static_cast<int>(tiles.size()));
    for (std::size_t i = 0; i < tiles.size(); i++) {
        w.occId[tiles[i]] = id;
        w.occK[tiles[i]] = static_cast<int>(tiles.size() - 1 - i);
        if (i > 0)
            w.touched.push_back(tiles[i]);
    }
}

// sealsTeammate as evaluateMoves asks it (the longest squeezed teammate's length per step, 0 none): the steps evaluateMove scores, and those with no survival penalty.
std::array<int, 4> seals(bot::World const& w, bot::Safety& safety, bot::Targets const& targets) {
    std::array<bool, 4> steps{}, survive{};
    for (int dir = 0; dir < 4; dir++) {
        auto const m = bot::evaluateMove(w, safety, targets, dir);
        steps[dir] = m.step;
        survive[dir] = m.survives;
    }
    return bot::sealsTeammate(w, safety, steps, survive);
}

// A dead-end corridor (y 4, x 1..5) opening at (6,4) onto open ground (x 6..15). A 2-long teammate walks east in it
// (head (3,4)); we stand just outside the mouth at (7,4), body trailing east. Stepping west onto the mouth leaves
// the teammate its next step but no way out; north or south keeps the mouth free when it gets there.
struct Corridor {
    bot::World w = test::world();

    Corridor() {
        enclose(w, [](int x, int y) { return (y == 4 && x >= 1 && x <= 5) || x >= 6; });
        test::body(w, {w.tileOf(7, 4), w.tileOf(8, 4), w.tileOf(9, 4), w.tileOf(10, 4)});
        teammate(w, 7, {w.tileOf(3, 4), w.tileOf(2, 4)});
    }
};

void stepOntoCorridorMouthSealsTeammate() {
    Corridor c;
    bot::Safety safety;
    safety.prepare(c.w);
    bot::Targets targets;
    targets.update(c.w);
    auto const moves = bot::evaluateMoves(c.w, safety, targets);
    require(!moves[W].friendExit, "the teammate still has its next step, so the only-exit guard doesn't see it");
    auto const seals = ::seals(c.w, safety, targets);
    require(seals[W], "taking the corridor's mouth leaves the teammate no way out");
    require(!seals[N] && !seals[S], "stepping aside keeps the mouth free for the teammate");
    require(moves[W].score <= moves[N].score - P.squeezePenalty / 2 && !moves[W].safe,
            "the sealing step pays the squeeze penalty");
    require(bot::bestOf(moves).dir != W, "we step aside rather than seal the teammate in");
}

// With P.squeezeOnValue a step that eats is charged only while the squeezed teammate's expected loss
// (P.squeezeDeathChance times its length) is more than the pearl: a pearl on the mouth is taken past the 2-long
// teammate; a step that doesn't eat is always charged.
void aPearlOutweighsASqueezedShortTeammate() {
    Corridor c;
    test::pearl(c.w, c.w.tileOf(6, 4));
    bot::Safety safety;
    safety.prepare(c.w);
    bot::Targets targets;
    targets.update(c.w);
    require(::seals(c.w, safety, targets)[W] == 2, "the step onto the mouth squeezes the 2-long teammate in");
    auto const moves = bot::evaluateMoves(c.w, safety, targets);
    if (!P.squeezeOnValue) {
        require(bot::bestOf(moves).dir != W, "without the price the pearl still loses to the guard");
        return;
    }
    require(bot::bestOf(moves).dir == W, "the pearl is worth more than what the 2-long teammate stands to lose");
    require(bot::squeezeCharged(2, false), "a step that doesn't eat is always charged");
}

void noPenaltyWhenEveryStepSeals() {
    Corridor c;
    test::wall(c.w, c.w.tileOf(7, 4), N);
    test::wall(c.w, c.w.tileOf(7, 4), S);
    bot::Safety safety;
    safety.prepare(c.w);
    bot::Targets targets;
    targets.update(c.w);
    auto const moves = bot::evaluateMoves(c.w, safety, targets);
    auto const seals = ::seals(c.w, safety, targets);
    require(!seals[W], "with the mouth our only step, no other step keeps the teammate's way out: no penalty");
    require(bot::bestOf(moves).dir == W, "our only step is still taken");
}

// North and south of our head are one-tile niches: dead ends for us, so they don't count as keeping the teammate's
// way out, and the safe step onto the corridor's mouth is not charged.
void unsafeStepsDoNotKeepTheWayOut() {
    Corridor c;
    for (int y : {3, 5}) {
        int const niche = c.w.tileOf(7, y);
        for (int dir : {N, E, S, W})
            if (c.w.step(niche, dir) != c.w.head)
                test::wall(c.w, niche, dir);
    }
    bot::Safety safety;
    safety.prepare(c.w);
    bot::Targets targets;
    targets.update(c.w);
    require(!bot::evaluateMove(c.w, safety, targets, N).survives, "the niche is a dead end for us");
    auto const seals = ::seals(c.w, safety, targets);
    require(!seals[W], "only an unsafe step keeps the teammate's way out: the safe step is not charged");
    require(bot::bestOf(bot::evaluateMoves(c.w, safety, targets)).dir == W, "we take the one safe step");
}

// A portal we have never seen through, in the corridor's north wall ahead of the teammate, is no way out (as in our own
// flood fill): taking the mouth still seals it in.
void unknownPortalIsNoWayOut() {
    Corridor c;
    c.w.rememberEdge(c.w.edgeOf(c.w.tileOf(4, 4), N), unswbc::Edge(false, unswbc::EdgeType::PORTAL, 9));
    require(c.w.step(c.w.tileOf(4, 4), N) == bot::UNKNOWN_PORTAL, "the corridor has an unknown portal");
    bot::Safety safety;
    safety.prepare(c.w);
    bot::Targets targets;
    targets.update(c.w);
    require(::seals(c.w, safety, targets)[W], "the unknown portal doesn't keep the teammate's way out");
}

void teammateInOpenGroundIsNeverSealed() {
    auto w = test::world();
    test::body(w, {w.tileOf(7, 4), w.tileOf(8, 4), w.tileOf(9, 4), w.tileOf(10, 4)});
    teammate(w, 7, {w.tileOf(5, 4), w.tileOf(4, 4)});
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    targets.update(w);
    auto const moves = bot::evaluateMoves(w, safety, targets);
    auto const seals = ::seals(w, safety, targets);
    require(!seals[N] && !seals[S] && !seals[W], "open ground has room past any one step of ours");
}

// A 1-wide room (y 3, x 1..4) whose only way out is a known portal on the east edge of (4,3), landing on (10,3) in
// an open region (x 10..15) where we stand. The teammate inside is 2 long with a free tile beside its head, so its
// next step is never ours; stepping west onto the landing shuts it in all the same.
void stepOntoPortalLandingSealsRoom() {
    auto w = test::world();
    enclose(w, [](int x, int y) { return (y == 3 && x >= 1 && x <= 4) || x >= 10; });
    w.rememberEdge(w.edgeOf(w.tileOf(4, 3), E), unswbc::Edge(false, unswbc::EdgeType::PORTAL, 5));
    w.rememberEdge(w.edgeOf(w.tileOf(10, 3), W), unswbc::Edge(false, unswbc::EdgeType::PORTAL, 5));
    require(w.step(w.tileOf(4, 3), E) == w.tileOf(10, 3), "the room's portal lands on (10,3)");
    test::body(w, {w.tileOf(11, 3), w.tileOf(12, 3), w.tileOf(13, 3), w.tileOf(14, 3)});
    teammate(w, 3, {w.tileOf(3, 3), w.tileOf(2, 3)});
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    targets.update(w);
    auto const moves = bot::evaluateMoves(w, safety, targets);
    auto const seals = ::seals(w, safety, targets);
    require(!moves[W].friendExit, "the teammate has a free tile beside its head");
    require(seals[W], "our head on the landing closes the room");
    require(!seals[N] && !seals[S], "the landing stays free when we step aside");
    require(bot::bestOf(moves).dir != W, "we leave the landing free");
}

} // namespace

int main() {
    return test::suite({
        {"a step onto a corridor's mouth seals the teammate in it", stepOntoCorridorMouthSealsTeammate},
        {"a pearl outweighs a squeezed short teammate", aPearlOutweighsASqueezedShortTeammate},
        {"no penalty when every step of ours seals the teammate", noPenaltyWhenEveryStepSeals},
        {"a step that is unsafe for us doesn't keep the teammate's way out", unsafeStepsDoNotKeepTheWayOut},
        {"an unknown portal is no way out", unknownPortalIsNoWayOut},
        {"a teammate in open ground is never sealed", teammateInOpenGroundIsNeverSealed},
        {"a step onto a room's portal landing seals the room", stepOntoPortalLandingSealsRoom},
    });
}
