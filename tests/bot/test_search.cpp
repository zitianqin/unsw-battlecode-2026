#include "support.hpp"
#include "scoring.hpp"
#include "search.hpp"

namespace {

constexpr int N = 0, E = 1, W = 3;

// An enemy dragon laid out head first, its tail in view unless the board hides it.
void enemy(bot::World& w, int id, std::vector<int> const& tiles) {
    int const len = static_cast<int>(tiles.size());
    for (int i = 0; i < len; i++) {
        int const t = tiles[i];
        w.occId[t] = id;
        w.occHead[t] = i == 0;
        w.occEnemy[t] = 1;
        w.occK[t] = len - 1 - i;
        if (i + 1 < len)
            w.behind[t] = tiles[i + 1];
        w.touched.push_back(t);
    }
    w.dragons.push_back({id, true, tiles.front(), len});
}

// Our length-12 dragon on a 32 x 32 board, head at (16,16), body trailing south.
bot::World longDragon() {
    auto w = test::world(32, 32);
    std::vector<int> tiles;
    for (int i = 0; i < 12; i++)
        tiles.push_back(w.tileOf(16, 16 + i));
    test::body(w, tiles);
    return w;
}

bot::SearchVerdict searchAll(bot::World const& w) {
    bot::Safety safety;
    safety.prepare(w);
    return bot::searchMoves(w, safety, {true, true, true, true});
}

// The E0108 post-mortem's case: a length-3 enemy head three steps from where our north move puts our head, in a
// line. Its sprint pays two segments, so it reaches only with a pearl on its way: then the north move is lost at the
// first reply; without the pearl it isn't, and neither is a step east, out of its reach.
bot::World pearlLine(bool pearl) {
    auto w = longDragon();
    enemy(w, 2, {w.tileOf(16, 12), w.tileOf(16, 11), w.tileOf(16, 10)});
    if (pearl)
        test::pearl(w, w.tileOf(16, 13));
    return w;
}

void pearlPaysTheSprint() {
    auto const paid = searchAll(pearlLine(true));
    test::require(paid.depth >= 1 && paid.lostAt[N] == 1, "a length-3 head eating a pearl on the way rams 3 steps");
    test::require(paid.lostAt[E] != 1 && paid.lostAt[W] != 1, "steps out of its reach aren't rammed at once");
    auto const unpaid = searchAll(pearlLine(false));
    test::require(unpaid.depth >= 1 && unpaid.lostAt[N] != 1, "without the pearl a length-3 head reaches 2 steps");
}

// The same case with the pearl tile out of sight: a fast tile there probably holds one, so it pays too.
void fastTileOutOfSightPays() {
    auto w = pearlLine(false);
    int const t = w.tileOf(16, 13);
    w.visStamp[t] = 0;
    test::fast(w, t);
    test::require(searchAll(w).lostAt[N] == 1, "an unseen fast tile pays a sprint step");
}

// A length-2 head can't pay for a sprint at all: two tiles off our next head it doesn't reach; a length-3 one does.
void lengthTwoOnlySteps() {
    for (int len : {2, 3}) {
        auto w = longDragon();
        std::vector<int> tiles{w.tileOf(16, 13)};
        for (int i = 1; i < len; i++)
            tiles.push_back(w.tileOf(16 - i, 13));
        enemy(w, 2, tiles);
        bool const lost = searchAll(w).lostAt[N] == 1;
        test::require(lost == (len == 3), len == 2 ? "a length-2 head steps only" : "a length-3 head sprints 2");
    }
}

// A head whose tail we can't confirm may be longer than it looks: a length-2 chain running out of sight counts as
// long enough to sprint (P.maxRamSteps + 1, as enemyReach assumes).
void hiddenTailMaySprint() {
    auto w = longDragon();
    enemy(w, 2, {w.tileOf(16, 13), w.tileOf(15, 13)});
    w.visStamp[w.tileOf(14, 13)] = 0; // the chain's end might go on west
    test::require(searchAll(w).lostAt[N] == 1, "an unconfirmed length-2 head may sprint 2 steps onto our next head");
}

// Whether a dragon searches: at least P.searchMinLength long, or with P.searchOnSwing while the ram of a head in
// reach would swing it more than P.searchMinSwing (the ram model's headOnSwing): a short dragon loses little to a
// shorter rammer, which dies too, and much food to steering clear. Our `len`-long dragon, head at (16,16) with a pearl
// 3 north, faces an enemy head `theirs` long 4 north.
bool searches(int len, int theirs) {
    auto w = test::world(32, 32);
    std::vector<int> tiles;
    for (int i = 0; i < len; i++)
        tiles.push_back(w.tileOf(16, 16 + i));
    test::body(w, tiles);
    std::vector<int> them;
    for (int i = 0; i < theirs; i++)
        them.push_back(w.tileOf(16, 12 - i));
    enemy(w, 2, them);
    test::pearl(w, w.tileOf(16, 13));
    return searchAll(w).depth > 0;
}

void shortDragonDoesNotSearch() {
    if (!P.searchOnSwing) {
        test::require(!searches(P.searchMinLength - 1, 3) && searches(P.searchMinLength, 3),
                      "a dragon searchMinLength long searches, one shorter doesn't");
        return;
    }
    test::require(!searches(6, 2), "a 6-long dragon doesn't search a 2-long rammer: the pearl is worth more");
    test::require(searches(12, 2), "a 12-long dragon does");
    test::require(!searches(8, 7), "an 8-long dragon beside a 7-long rammer doesn't: the ram gains them little");
    test::require(searches(14, 7), "a 14-long one beside it does");
}

// I104's case: our north move leads into a spot whose only exit is east (kelp north and west of it); a length-3
// enemy head 3 steps east of that exit can't reach it by one step a reply, but a 2-step sprint does it after one
// step closer. So north is lost at our second move, while east keeps an exit out of its reach, and east is played.
// A length-2 head, stepping only, can't close the gap in time.
bot::World pocketExit(int enemyLen) {
    auto w = longDragon();
    int const nook = w.tileOf(16, 15);
    test::wall(w, nook, N);
    test::wall(w, nook, W);
    std::vector<int> tiles;
    for (int i = 0; i < enemyLen; i++)
        tiles.push_back(w.tileOf(20 + i, 15));
    enemy(w, 2, tiles);
    return w;
}

void sprintClosesTheExit() {
    auto const w = pocketExit(3);
    auto const verdict = searchAll(w);
    test::require(verdict.depth >= 2 && verdict.lostAt[N] == 2, "the sprint closes the nook's exit on our 2nd move");
    test::require(verdict.lostAt[E] == 0 || verdict.lostAt[E] > 2, "east keeps an exit past our 2nd move");
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    auto const moves = bot::evaluateMoves(w, safety, targets);
    test::require(moves[N].lostAt == 2 && moves[N].score < moves[E].score - P.searchLossPenalty / 2,
                  "the forced move costs the search's penalty");
    test::require(bot::bestOf(moves, N).dir != N, "we don't take the nook even when heading north");
}

void steppingHeadIsTooSlow() {
    auto const verdict = searchAll(pocketExit(2));
    test::require(verdict.lostAt[N] != 1 && verdict.lostAt[N] != 2, "a length-2 head can't close the exit in time");
}

// Boundaries: no enemy head in view gives no verdict and changes no score; an enemy head as long as us is no bad
// rammer, so its reach onto our head isn't a loss (it isn't searched at all).
void noEnemyNoVerdict() {
    auto const w = longDragon();
    test::require(searchAll(w).depth == 0, "no enemy head, no search");
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    auto const moves = bot::evaluateMoves(w, safety, targets);
    for (auto const& m : moves)
        test::require(m.lostAt == 0, "no move is lost with no enemy");
}

void longerHeadIsNoRammer() {
    auto w = longDragon();
    std::vector<int> tiles;
    for (int i = 0; i < 12; i++)
        tiles.push_back(w.tileOf(16, 13 - i < 0 ? 13 - i + 32 : 13 - i));
    enemy(w, 2, tiles);
    auto const verdict = searchAll(w);
    test::require(verdict.lostAt[N] == 0, "an equal head landing on ours is no loss");
    auto shorter = pearlLine(true);
    test::require(searchAll(shorter).lostAt[N] == 1, "while the length-3 head in the same place rams");
}

// When every searched move is lost at the first reply (a length-3 head two steps from both open moves), the scores
// stand: the search can't tell the moves apart. The west move, into a 1-tile dead end, is closed and not searched;
// it must not make the open moves look worse by counting as never lost.
void everyMoveLostKeepsScores() {
    auto w = longDragon();
    int const nook = w.tileOf(15, 16);
    test::wall(w, nook, N);
    test::wall(w, nook, W);
    test::wall(w, nook, 2);
    enemy(w, 2, {w.tileOf(17, 14), w.tileOf(18, 14), w.tileOf(19, 14)});
    // N (16,15) and E (17,16) are each 2 steps from its head: both rammed at once.
    auto const verdict = searchAll(w);
    test::require(verdict.lostAt[N] == 1 && verdict.lostAt[E] == 1, "both open moves are rammed at once");
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    auto const moves = bot::evaluateMoves(w, safety, targets);
    test::require(moves[W].closed && moves[W].score > P.doomedScore, "the dead end is a closed, unsearched move");
    test::require(moves[N].terms[bot::TERM_PENALTY] == 0 && moves[E].terms[bot::TERM_PENALTY] == 0,
                  "the search charges neither when both are lost as soon");
}

// A head far away on the torus but beside a known portal that opens next to us is searched: its ram through the
// portal is 3 steps (cross, step, step onto our next head), in a length-4 head's reach.
void rammerThroughPortal() {
    auto w = longDragon();
    enemy(w, 2, {w.tileOf(6, 5), w.tileOf(5, 5), w.tileOf(4, 5), w.tileOf(3, 5)});
    w.setEdge(w.edgeOf(w.tileOf(6, 5), E), bot::EDGE_PORTAL, 9);
    w.setEdge(w.edgeOf(w.tileOf(16, 13), W), bot::EDGE_PORTAL, 9);
    test::require(w.step(w.tileOf(6, 5), E) == w.tileOf(16, 13), "the portal leads beside us");
    test::require(searchAll(w).lostAt[N] == 1, "a length-4 head rams through the portal");
}

} // namespace

int main() {
    return test::suite({
        {"a pearl pays an enemy's sprint step", pearlPaysTheSprint},
        {"an unseen fast tile pays one too", fastTileOutOfSightPays},
        {"a length-2 head only steps", lengthTwoOnlySteps},
        {"an unconfirmed tail may be longer", hiddenTailMaySprint},
        {"a short dragon doesn't search", shortDragonDoesNotSearch},
        {"a sprint closes our exit a move ahead", sprintClosesTheExit},
        {"a stepping head is too slow for it", steppingHeadIsTooSlow},
        {"no enemy, no verdict", noEnemyNoVerdict},
        {"a head as long as ours is no rammer", longerHeadIsNoRammer},
        {"every move lost as soon keeps the scores", everyMoveLostKeepsScores},
        {"a rammer through a portal is searched", rammerThroughPortal},
    });
}
