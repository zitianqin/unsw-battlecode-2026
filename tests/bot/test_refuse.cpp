#include "support.hpp"
#include "refuse.hpp"

// A pearl target whose approach the move scorer refuses (the step onto it, or with P.refuseApproach any move nearer
// it) is dropped for the next target (refuse.hpp), so the dragon stops hovering beside a pearl it won't take (I303).

namespace {

struct Plan {
    bot::Targets targets;
    std::array<bot::MoveEval, 4> moves;
    int first = -1;       // the turn's first pearl target
    bool dropped = false;
    bot::MoveEval best() const { return bot::bestOf(moves); }
};

// main's order: targets, the moves, and once more after a refused target is dropped.
Plan plan(bot::World const& w) {
    Plan p;
    bot::Safety safety;
    safety.prepare(w);
    p.targets.update(w);
    p.first = p.targets.pearl;
    p.moves = bot::evaluateMoves(w, safety, p.targets);
    p.dropped = bot::dropRefusedTarget(w, p.targets, p.moves);
    if (p.dropped)
        p.moves = bot::evaluateMoves(w, safety, p.targets);
    return p;
}

// A length-6 dragon at (8,8) facing east on a 16 x 16 board, a pearl east at (9,8) at the mouth of a corridor
// (9..12, 8) walled north and south whose only way out is (13,8), where a teammate head at (13,7) can step first
// (jam.hpp's cut-off), and a second pearl 5 open steps north at (8,3).
bot::World cutOffPearl(int unitCount) {
    auto w = test::world();
    test::body(w, {w.tileOf(8, 8), w.tileOf(7, 8), w.tileOf(6, 8), w.tileOf(5, 8), w.tileOf(4, 8), w.tileOf(3, 8)});
    w.facing[w.head] = 1;
    for (int x = 9; x <= 12; x++) {
        test::wall(w, w.tileOf(x, 8), 0);
        test::wall(w, w.tileOf(x, 8), 2);
    }
    test::pearl(w, w.tileOf(9, 8));
    test::pearl(w, w.tileOf(8, 3));
    test::dragon(w, 2, false, w.tileOf(13, 7), 4);
    w.unitCount = unitCount;
    return w;
}

void cutOffPearlIsReplacedAtTheCap() {
    auto const w = cutOffPearl(64 - 1);
    auto const p = plan(w);
    test::require(p.first == w.tileOf(9, 8), "the nearest pearl is the first target");
    test::require(p.dropped && p.targets.pearl == w.tileOf(8, 3), "its cut-off step is refused: the second pearl is the target");
    test::require(p.best().dir == 0, "the dragon heads north for the second pearl instead of hovering");
}

void belowTheCapMarginThePearlIsEaten() {
    auto const w = cutOffPearl(64 - P.headReachCapMargin - 1);
    auto const p = plan(w);
    test::require(!p.dropped && p.targets.pearl == w.tileOf(9, 8) && p.best().dir == 1,
                  "below the cap margin nothing cuts the step off: the first pearl is eaten");
}

// A dragon of `length` at (12,12) facing east beside a dead end x 13..17 on row 12 (walled north, south and at its
// end) holding `pearls` pearls from `depth` tiles in inwards (1: from the mouth), on a 24 x 24 board whose columns
// x < 4 we have never seen.
bot::World besidePocket(int length, int pearls, int depth = 1) {
    auto w = test::world(24, 24);
    std::vector<int> tiles;
    for (int i = 0; i < length; i++)
        tiles.push_back(w.tileOf(12 - i, 12));
    test::body(w, tiles);
    w.facing[w.head] = 1;
    for (int x = 13; x <= 17; x++) {
        test::wall(w, w.tileOf(x, 12), 0);
        test::wall(w, w.tileOf(x, 12), 2);
    }
    test::wall(w, w.tileOf(17, 12), 1);
    for (int x = 12 + depth; x < 12 + depth + pearls; x++)
        test::pearl(w, w.tileOf(x, 12));
    for (int y = 0; y < w.H; y++)
        for (int x = 0; x < 4; x++) {
            w.visStamp[w.tileOf(x, y)] = 0;
            w.lastSeen[w.tileOf(x, y)] = -1;
        }
    return w;
}

void lonePocketPearlGivesWayToExploring() {
    auto const w = besidePocket(3, 1);
    auto const p = plan(w);
    test::require(p.first == w.tileOf(13, 12), "the pocket's pearl is the first target");
    test::require(p.moves[1].closed && !p.moves[1].farm, "farmEntryOk turns the one-pearl pocket down");
    test::require(p.dropped && p.targets.pearl < 0 && p.targets.explore >= 0,
                  "with nothing else in reach the dragon explores instead of circling beside it");
    test::require(p.best().dir != 1, "and still stays out of the pocket");
}

void pocketPearlGivesWayToAnotherPearl() {
    auto w = besidePocket(3, 1);
    test::pearl(w, w.tileOf(12, 17));
    auto const p = plan(w);
    test::require(p.dropped && p.targets.pearl == w.tileOf(12, 17) && p.best().dir == 2,
                  "the pearl 5 open steps south replaces the refused pocket pearl");
}

// I336's case: the pearl lies 2 tiles into a dead end, so the refused move is the pocket's mouth, not the step onto it.
void deeperPocketPearlIsReplaced() {
    auto w = besidePocket(3, 1, 2);
    test::pearl(w, w.tileOf(12, 17));
    auto const p = plan(w);
    test::require(p.first == w.tileOf(14, 12), "the pearl 2 deep in the pocket is the first target");
    test::require(P.refuseApproach == (p.dropped && p.targets.pearl == w.tileOf(12, 17) && p.best().dir == 2),
                  "with refuseApproach its refused mouth drops it for the open pearl 5 steps south");
}

// The farm boundary (farmEntryOk): a dragon long enough to split out, beside farmMinPearls - 1 pearls, drops the
// target; beside farmMinPearls it enters and farms as before.
void farmBoundary() {
    int const length = std::max(2, P.farmMinEndLength);
    auto const few = plan(besidePocket(length, P.farmMinPearls - 1));
    test::require(few.dropped && few.best().dir != 1, "one pearl short of a farm, the pocket pearl is replaced");
    auto const enough = plan(besidePocket(length, P.farmMinPearls));
    test::require(!enough.dropped && enough.best().dir == 1 && enough.moves[1].farm,
                  "a pocket farmEntryOk accepts is still entered and farmed");
}

// A pocket's refusal lasts P.refusedPocketRounds rounds after the one it happened in; then the tile can be chosen
// again. A cut-off's lasts only its own turn.
void refusalExpires() {
    auto w = besidePocket(3, 1);
    int const pocketPearl = w.tileOf(13, 12);
    auto const p = plan(w);
    test::require(p.dropped, "the pocket pearl is refused");
    bot::Targets targets = p.targets;
    w.round += P.refusedPocketRounds;
    targets.update(w);
    test::require(targets.pearl != pocketPearl, "within P.refusedPocketRounds the refused pocket pearl is skipped");
    w.round++;
    targets.update(w);
    test::require(targets.pearl == pocketPearl, "after it the pearl can be the target again");

    auto cut = cutOffPearl(64 - 1);
    auto const q = plan(cut);
    test::require(q.dropped, "the cut-off pearl is refused");
    bot::Targets next = q.targets;
    cut.round++;
    next.update(cut);
    test::require(next.pearl == cut.tileOf(9, 8), "the next turn a cut-off pearl can be the target again");
}

// Only a refused step counts: a target the best move steps onto (whatever its small penalties), a step whose only
// penalty is the out-of-sight term, a step that is fatal only for now, a fold's king or a feeder (not choosePearl's
// pick), and a turn with no other safe move all leave the target alone.
void onlyARefusedPickIsDropped() {
    auto const w = besidePocket(3, 1);
    int const t = w.tileOf(13, 12);
    bot::Targets targets;
    targets.pearl = targets.picked = t;
    targets.pearlField.build(w, t, 20);
    std::array<bot::MoveEval, 4> moves;
    for (int dir = 0; dir < 4; dir++) {
        moves[dir].dir = dir;
        moves[dir].dest = w.step(w.head, dir);
        moves[dir].safe = true;
        moves[dir].score = 0;
    }
    moves[3].score = P.lethalScore;
    moves[3].safe = false;
    moves[1].safe = false;
    moves[1].score = moves[1].terms[bot::TERM_PENALTY] = -P.shortfallPerTile;
    test::require(bot::targetRefused(w, targets, moves), "an unsafe step onto the target that loses is refused");
    moves[1].score = moves[1].terms[bot::TERM_PENALTY] = P.lethalScore;
    test::require(!bot::targetRefused(w, targets, moves), "a body still on the pearl is no refusal: it moves on");
    moves[1].score = moves[1].terms[bot::TERM_PENALTY] = -P.unseenShortfallPerTile;
    test::require(!bot::targetRefused(w, targets, moves), "an out-of-sight shortfall alone is no refusal");
    moves[1].terms[bot::TERM_PENALTY] = -P.shortfallPerTile;
    moves[1].score = 10;
    test::require(!bot::targetRefused(w, targets, moves), "an unsafe step the scorer still takes is not refused");
    moves[1].score = -P.shortfallPerTile;
    targets.picked = -1;
    test::require(!bot::targetRefused(w, targets, moves), "a target a fold or feeder set is not choosePearl's to drop");
    targets.picked = t;
    moves[0].safe = moves[2].safe = false;
    test::require(!bot::targetRefused(w, targets, moves), "with no other safe move nothing is dropped");
}

} // namespace

int main() {
    return test::suite({
        {"a cut-off pearl is replaced at the cap", cutOffPearlIsReplacedAtTheCap},
        {"below the cap margin the pearl is eaten", belowTheCapMarginThePearlIsEaten},
        {"a lone pocket pearl gives way to exploring", lonePocketPearlGivesWayToExploring},
        {"a pocket pearl gives way to another pearl", pocketPearlGivesWayToAnotherPearl},
        {"a pearl deeper in a pocket is replaced", deeperPocketPearlIsReplaced},
        {"one pearl short of a farm is replaced, a farm is entered", farmBoundary},
        {"a refusal expires", refusalExpires},
        {"only a refused pick is dropped", onlyARefusedPickIsDropped},
    });
}
