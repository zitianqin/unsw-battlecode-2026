#include "support.hpp"
#include "ram.hpp"
#include "scoring.hpp"

namespace {

// Our length-`length` dragon with its head at (8,8), body trailing west.
bot::World dragonOfLength(int length) {
    auto w = test::world();
    std::vector<int> tiles;
    for (int i = 0; i < length; ++i)
        tiles.push_back(w.tileOf(8 - i, 8));
    test::body(w, tiles);
    return w;
}

bot::Ram ramFor(bot::World const& w) {
    bot::Safety safety;
    safety.prepare(w);
    return bot::findRam(w, safety);
}

// Where the ram's steps end.
int endOf(bot::World const& w, bot::Ram const& ram) {
    int at = w.head;
    for (int dir : ram.steps)
        at = w.step(at, dir);
    return at;
}

void sprintsTwoStepsStraightIntoLongerHead() {
    auto w = dragonOfLength(4);
    test::dragon(w, 2, true, w.tileOf(10, 8), 6);
    auto const ram = ramFor(w);
    test::require(ram.steps == std::vector<int>{1, 1}, "a length-6 head two free tiles east is rammed with EE");
}

void sprintsAroundACorner() {
    auto w = dragonOfLength(4);
    test::dragon(w, 2, true, w.tileOf(9, 7), 6);
    auto const ram = ramFor(w);
    test::require(ram.steps.size() == 2 && endOf(w, ram) == w.tileOf(9, 7),
                  "an L-shaped two-step path reaches the diagonal head");
}

void sprintsThreeStepsWhenLongEnough() {
    auto w = dragonOfLength(4);
    test::dragon(w, 2, true, w.tileOf(11, 8), 6);
    test::require(ramFor(w).steps == std::vector<int>{1, 1, 1}, "length 4 can pay for a three-step sprint");
}

void doesNotRamShorterHead() {
    auto w = dragonOfLength(4);
    test::dragon(w, 2, true, w.tileOf(10, 8), 3);
    test::require(ramFor(w).steps.empty(), "trading our four segments for three is a bad trade");
}

void bodyOnOnlyPathBlocksRam() {
    auto w = dragonOfLength(4);
    test::dragon(w, 2, true, w.tileOf(10, 8), 6);
    w.occId[w.tileOf(9, 8)] = 3;
    w.occEnemy[w.tileOf(9, 8)] = 1;
    w.touched.push_back(w.tileOf(9, 8));
    test::require(ramFor(w).steps.empty(), "the only two-step path runs through a body, which would kill us");
}

void shortDragonCannotPayForSprint() {
    auto two = dragonOfLength(2);
    test::dragon(two, 2, true, two.tileOf(10, 8), 6);
    test::require(ramFor(two).steps.empty(), "a length-2 dragon cannot pay for a second step");
    test::pearl(two, two.tileOf(9, 8));
    test::require(ramFor(two).steps == std::vector<int>{1, 1},
                  "a pearl on the first step grows us to 3, which pays for the second");

    auto three = dragonOfLength(3);
    test::dragon(three, 2, true, three.tileOf(11, 8), 6);
    test::require(ramFor(three).steps.empty(), "length 3 pays for two steps, not three");
}

void beyondStartingLengthIsOutOfReach() {
    auto w = dragonOfLength(4);
    test::dragon(w, 2, true, w.tileOf(12, 8), 10);
    test::require(ramFor(w).steps.empty(), "without pearls a length-4 dragon cannot pay for four steps");
}

void protectedDragonNeverRams() {
    auto w = dragonOfLength(6);
    w.round = P.mergeRound + 1;
    test::dragon(w, 2, true, w.tileOf(10, 8), 10);
    test::require(ramFor(w).steps.empty(), "our late-game king keeps its length instead of trading");
}

void friendlyHeadIsNeverRammed() {
    auto w = dragonOfLength(4);
    test::dragon(w, 2, false, w.tileOf(10, 8), 6);
    test::require(ramFor(w).steps.empty(), "a teammate's head is not a target");
}

void prefersBestSwingThenFewestSteps() {
    auto w = dragonOfLength(4);
    test::dragon(w, 2, true, w.tileOf(8, 7), 5);
    test::dragon(w, 3, true, w.tileOf(10, 8), 8);
    test::dragon(w, 4, false, w.tileOf(2, 2), 3); // edge 0
    auto const ram = ramFor(w);
    test::require(ram.victimLen == 8 && ram.steps.size() == 2,
                  "the length-8 head two steps away beats the adjacent length-5 head, both worth ramming");
    auto near = dragonOfLength(4);
    test::dragon(near, 2, true, near.tileOf(9, 8), 6);
    test::dragon(near, 3, true, near.tileOf(8, 6), 6);
    test::dragon(near, 4, false, near.tileOf(2, 2), 3); // edge 0, as above
    test::require(ramFor(near).steps == std::vector<int>{1}, "between equally good victims the one-step ram wins");
}

// Outnumbered 3 to 4: a length-7 head three steps east with two more enemy heads beside it, and a length-5 head two
// steps north with two of our heads beside it. The hand rule rams the longest it may (the 7, 3 longer while
// outnumbered); the model rams where our team will eat the drop.
void modelPrefersTheDropWeEatOverTheLongestVictim() {
    auto w = dragonOfLength(4);
    test::dragon(w, 2, true, w.tileOf(11, 8), 7);
    test::dragon(w, 3, true, w.tileOf(12, 7), 2);
    test::dragon(w, 5, true, w.tileOf(12, 9), 2);
    test::dragon(w, 6, true, w.tileOf(8, 6), 5);
    test::dragon(w, 7, false, w.tileOf(7, 5), 3);
    test::dragon(w, 8, false, w.tileOf(9, 5), 3);
    auto const ram = ramFor(w);
    test::require(ram.steps == std::vector<int>{0, 0} && ram.victimLen == 5,
                  "the length-5 head beside two of ours is rammed, not the length-7 one beside two of theirs");
}

// Score of the one-step move `dir` (the attack path through evaluateMove).
double oneStepScore(bot::World const& w, int dir) {
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    return bot::evaluateMove(w, safety, targets, dir).score;
}

int dirTo(bot::World const& w, int tile) {
    for (int dir = 0; dir < 4; dir++)
        if (w.step(w.head, dir) == tile)
            return dir;
    return -1;
}

// Length 3, alone against two length-3 enemy heads: one adjacent, one two free steps east.
bot::World outnumberedByEqualHeads() {
    auto w = dragonOfLength(3);
    test::dragon(w, 2, true, w.tileOf(8, 7), 3);
    test::dragon(w, 3, true, w.tileOf(10, 8), 3);
    return w;
}

void outnumberedSkipsEvenTrades() {
    auto w = outnumberedByEqualHeads();
    test::require(ramFor(w).steps.empty(), "outnumbered 1 to 2, neither equal-length head is rammed");
    int const adjacent = dirTo(w, w.tileOf(8, 7));
    test::require(oneStepScore(w, adjacent) <= P.badTradeScore + 3, "the adjacent equal head scores as a bad trade");
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    auto const best = bot::bestMove(w, safety, targets);
    test::require(best.dest != w.tileOf(8, 7) && best.score > P.doomedScore, "a safe move is taken instead");
}

void localSuperiorityStillRams() {
    auto w = dragonOfLength(3);
    test::dragon(w, 2, true, w.tileOf(8, 7), 3);
    test::dragon(w, 4, false, w.tileOf(2, 2), 3);
    test::dragon(w, 5, false, w.tileOf(6, 10), 3);
    test::require(ramFor(w).steps == std::vector<int>{dirTo(w, w.tileOf(8, 7))},
                  "a teammate two steps from our tail's drop: the adjacent equal head is rammed");
    test::require(oneStepScore(w, dirTo(w, w.tileOf(8, 7))) >= P.attackBonus, "and scored as an attack");

    // P.evenTradeHarvest: the same head count with our heads too far to reach the sprint's drop first.
    auto far = dragonOfLength(3);
    test::dragon(far, 2, true, far.tileOf(10, 8), 3);
    test::dragon(far, 4, false, far.tileOf(2, 2), 3);
    test::dragon(far, 5, false, far.tileOf(2, 12), 3);
    test::require(ramFor(far).steps.empty(), "the equal head two steps east is left alone: nobody of ours eats its drop");
    auto near = dragonOfLength(3);
    test::dragon(near, 2, true, near.tileOf(10, 8), 3);
    test::dragon(near, 4, false, near.tileOf(11, 6), 3);
    test::require(ramFor(near).steps == std::vector<int>{1, 1}, "with a teammate beside its drop it is sprint-rammed");
}

// The learned gate (P.ramModel) prices a ram by who is near its drop, not by length alone. Our length 4, a length-3
// enemy head two free steps east (a victim 1 shorter, which the hand rule never rams), and `friends` teammate heads
// beside that victim, within P.dropEdgeRadius of it, to eat the drop.
bot::World shorterVictimWithFriendsNear(int friends) {
    auto w = dragonOfLength(4);
    test::dragon(w, 2, true, w.tileOf(10, 8), 3);
    int const near[] = {w.tileOf(11, 7), w.tileOf(11, 9)};
    for (int k = 0; k < friends; k++)
        test::dragon(w, 4 + k, false, near[k], 3);
    return w;
}

void modelRamsShorterHeadItsTeamWillEat() {
    auto w = shorterVictimWithFriendsNear(2);
    test::require(ramFor(w).steps == std::vector<int>{1, 1},
                  "two teammates beside the drop make a 1-shorter victim worth the sprint");
    auto alone = shorterVictimWithFriendsNear(0);
    test::require(ramFor(alone).steps.empty(), "alone, the same victim is not worth our longer body");
}

void modelKeepsTheTradeChecks() {
    auto few = shorterVictimWithFriendsNear(2);
    few.unitCount = P.minUnitsToTrade - 1;
    test::require(ramFor(few).steps.empty(), "a team below minUnitsToTrade never trades, however good the drop");
    auto king = shorterVictimWithFriendsNear(2);
    king.length = P.protectMinLength;
    king.dragons[1].visibleLen = king.length + 1;
    king.round = P.mergeRound - 1;
    test::require(!ramFor(king).steps.empty(), "before mergeRound a longer victim with the drop ours is rammed");
    king.round = P.mergeRound + 1;
    test::require(ramFor(king).steps.empty(), "our protected late-game dragon never trades");
}

// Length 3, one teammate far off, and a length-3 enemy head adjacent to the north: an even trade at head edge 0.
// Another enemy head stands `apart` steps from the victim.
bot::World evenTradeWithEnemyApart(int apart) {
    auto w = dragonOfLength(3);
    test::dragon(w, 2, true, w.tileOf(8, 7), 3);
    test::dragon(w, 3, true, w.tileOf(8 + apart, 7), 2);
    test::dragon(w, 4, false, w.tileOf(2, 13), 3);
    return w;
}

void evenTradeDeclinedWhenTheirTeamEatsTheDrop() {
    auto w = evenTradeWithEnemyApart(P.dropEdgeRadius);
    int const adjacent = dirTo(w, w.tileOf(8, 7));
    test::require(ramFor(w).steps.empty(), "an enemy head beside the drop makes the even trade a loss");
    test::require(oneStepScore(w, adjacent) <= P.badTradeScore + 3, "and the one-step path scores it as a bad trade");
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    auto const best = bot::bestMove(w, safety, targets);
    test::require(best.dest != w.tileOf(8, 7) && best.score > P.doomedScore, "a safe scored move is played instead");

    // The model would keep it (that head is beyond P.dropEdgeRadius), but it still reaches the drop before our
    // far teammate: P.evenTradeHarvest declines.
    auto far = evenTradeWithEnemyApart(P.dropEdgeRadius + 3);
    test::require(ramFor(far).steps.empty(), "with that head beyond the model's radius but first to the drop, no trade");
}

// P.evenTradeHarvest's boundary: one against one, no other head in view, so nobody is first to any drop tile (0 - 0).
void tiedHeadsWithNobodyNearDoNotTrade() {
    auto w = dragonOfLength(3);
    test::dragon(w, 2, true, w.tileOf(8, 7), 3);
    int const adjacent = dirTo(w, w.tileOf(8, 7));
    test::require(ramFor(w).steps.empty(), "one against one with nobody to eat the drop: no even trade");
    test::require(oneStepScore(w, adjacent) <= P.badTradeScore + 3, "and the one-step path scores it as a bad trade");
    auto longer = dragonOfLength(3);
    test::dragon(longer, 2, true, longer.tileOf(8, 7), 4);
    test::require(ramFor(longer).steps == std::vector<int>{adjacent}, "a longer victim is still rammed");
}

// Adds an enemy dragon: its head at `tiles[0]`, then its visible body, linked head to tail as vision links it.
void enemyChain(bot::World& w, int id, std::vector<int> const& tiles) {
    test::dragon(w, id, true, tiles[0], static_cast<int>(tiles.size()));
    for (std::size_t i = 1; i < tiles.size(); i++) {
        w.occId[tiles[i]] = id;
        w.occEnemy[tiles[i]] = 1;
        w.touched.push_back(tiles[i]);
        w.behind[tiles[i - 1]] = tiles[i];
    }
}

// Our length 4 at (8,8), a length-4 enemy head adjacent to the north with its body running north to (8,4), a head of
// `nearEnemy`'s team two tiles east of that body at (10,5) and a head of the other team far off at (0,15): head
// edge 0 either way.
bot::World evenVictimWithHeadBesideItsBody(bool nearEnemy, int victimLen = 4) {
    auto w = dragonOfLength(4);
    enemyChain(w, 2, {w.tileOf(8, 7), w.tileOf(8, 6), w.tileOf(8, 5), w.tileOf(8, 4)});
    w.dragons[1].visibleLen = victimLen;
    test::dragon(w, 3, nearEnemy, w.tileOf(10, 5), 3);
    test::dragon(w, 4, !nearEnemy, w.tileOf(0, 15), 3);
    return w;
}

void evenRamGoesToWhoEatsTheDrop() {
    auto ours = evenVictimWithHeadBesideItsBody(false);
    int const north = dirTo(ours, ours.tileOf(8, 7));
    bot::Safety safety;
    safety.prepare(ours);
    test::require(safety.headEdge == 0, "two heads a side");
    test::require(bot::dropHarvestEdge(ours, ours.tileOf(8, 7), {north}) == 4, "our teammate is first to all 4 drops");
    test::require(ramFor(ours).steps == std::vector<int>{north}, "our teammate eats the drop: the even head is rammed");

    auto theirs = evenVictimWithHeadBesideItsBody(true);
    test::require(bot::dropHarvestEdge(theirs, theirs.tileOf(8, 7), {north}) == -4, "their head is first to all 4");
    test::require(ramFor(theirs).steps.empty(), "their head eats the drop: the same even head is left alone");
    test::require(oneStepScore(theirs, north) <= P.badTradeScore + 4, "and the one-step path scores a bad trade");

    auto longer = evenVictimWithHeadBesideItsBody(true, 5);
    test::require(ramFor(longer).steps == std::vector<int>{north}, "a longer victim is rammed whoever eats the drop");
}

// The drop of a 3-step sprint ram by our length-5 dragon: the victim's head and every second segment of its visible
// chain, and of our body as the fatal third step finds it: two steps taken and one tail segment shed (length 4), so
// our head on the second step's tile, then the first step's, then our old head and the segment behind it.
void ramDropTilesFollowTheSprint() {
    auto w = dragonOfLength(5);
    enemyChain(w, 2, {w.tileOf(11, 8), w.tileOf(12, 8), w.tileOf(13, 8)});
    std::vector<int> const want = {w.tileOf(11, 8), w.tileOf(13, 8), w.tileOf(10, 8), w.tileOf(8, 8)};
    test::require(bot::ramDropTiles(w, w.tileOf(11, 8), {1, 1, 1}) == want,
                  "victim head, its third segment, our head a tile short of it and our old head");
    test::pearl(w, w.tileOf(9, 8));
    std::vector<int> const fed = {w.tileOf(11, 8), w.tileOf(13, 8), w.tileOf(10, 8), w.tileOf(8, 8), w.tileOf(6, 8)};
    test::require(bot::ramDropTiles(w, w.tileOf(11, 8), {1, 1, 1}) == fed,
                  "a pearl on the way pays the shed segment back: our fifth segment drops too");
}

} // namespace

int main() {
    return test::suite({
        {"two-step straight ram", sprintsTwoStepsStraightIntoLongerHead},
        {"two-step corner ram", sprintsAroundACorner},
        {"three-step ram", sprintsThreeStepsWhenLongEnough},
        {"shorter head is not rammed", doesNotRamShorterHead},
        {"body on the path blocks the ram", bodyOnOnlyPathBlocksRam},
        {"sprint must be paid for", shortDragonCannotPayForSprint},
        {"ram beyond starting length is out of reach", beyondStartingLengthIsOutOfReach},
        {"protected dragon never rams", protectedDragonNeverRams},
        {"teammate head is not rammed", friendlyHeadIsNeverRammed},
        {"best swing, then fewest steps", prefersBestSwingThenFewestSteps},
        {"model: the drop we eat over the longest victim", modelPrefersTheDropWeEatOverTheLongestVictim},
        {"outnumbered: no even trades", outnumberedSkipsEvenTrades},
        {"local superiority: even trades on both paths, if ours eat the drop", localSuperiorityStillRams},
        {"model: shorter head rammed when our team eats the drop", modelRamsShorterHeadItsTeamWillEat},
        {"model: trade checks still apply", modelKeepsTheTradeChecks},
        {"even trade declined when their team eats the drop", evenTradeDeclinedWhenTheirTeamEatsTheDrop},
        {"tied heads, nobody near the drop: no even trade", tiedHeadsWithNobodyNearDoNotTrade},
        {"even ram goes to whoever eats the drop", evenRamGoesToWhoEatsTheDrop},
        {"ram drop tiles follow the sprint", ramDropTilesFollowTheSprint},
    });
}
