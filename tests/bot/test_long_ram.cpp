#include "support.hpp"
#include "escape.hpp"
#include "hunt.hpp"
#include "ram.hpp"

namespace {

// A straight visible corridor isolates the sprint's length and blocking conditions.
bot::World corridor(int length, int distance) {
    auto w = test::world(32, 32);
    std::vector<int> body;
    for (int i = 0; i < length; ++i)
        body.push_back(w.tileOf(16 - i, 16));
    test::body(w, body);
    for (int x = 16; x <= 16 + distance; ++x) {
        test::wall(w, w.tileOf(x, 16), 0);
        test::wall(w, w.tileOf(x, 16), 2);
    }
    test::dragon(w, 2, true, w.tileOf(16 + distance, 16), length + P.outnumberedAttackMinGain);
    return w;
}

bot::Ram ramFor(bot::World const& w) {
    bot::Safety safety;
    safety.prepare(w);
    return bot::findRam(w, safety);
}

void longSprintAndLengthBoundary() {
    int const distance = P.ownRamSteps - 1;
    auto enough = corridor(P.ownRamSteps + 1, distance);
    test::require(ramFor(enough).steps == std::vector<int>(distance, 1),
                  "a long dragon reaches the longer head along the entire corridor");
    auto shortOne = corridor(distance, distance);
    test::require(ramFor(shortOne).steps.empty(), "one segment too short cannot fund that ram");
    auto boundary = corridor(P.ownRamSteps + 1, P.ownRamSteps);
    test::require(ramFor(boundary).steps.size() == P.ownRamSteps, "the configured reach is inclusive");
    auto beyond = corridor(P.ownRamSteps + 2, P.ownRamSteps + 1);
    test::require(ramFor(beyond).steps.empty(), "spare length does not bypass the configured cap");
}

void blockedAndUnseenPaths() {
    for (bool enemy : {false, true}) {
        auto w = corridor(P.ownRamSteps + 1, P.ownRamSteps - 1);
        int const at = w.tileOf(18, 16);
        w.occId[at] = 3;
        w.occEnemy[at] = enemy;
        test::require(ramFor(w).steps.empty(), "an allied or enemy body blocks a long sprint");
    }
    auto friendlyHead = corridor(P.ownRamSteps + 1, P.ownRamSteps - 1);
    test::dragon(friendlyHead, 3, false, friendlyHead.tileOf(18, 16), 2);
    test::require(ramFor(friendlyHead).steps.empty(), "a friendly head on the route blocks the ram");
    auto unseen = corridor(P.ownRamSteps + 1, P.ownRamSteps - 1);
    unseen.visStamp[unseen.tileOf(18, 16)] = 0;
    test::require(ramFor(unseen).steps.empty(), "remembered free tiles do not authorize a blind sprint");
}

void diagonalHeadInsideNormalVision() {
    auto w = test::world();
    std::vector<int> body;
    for (int i = 0; i <= P.ownRamSteps; ++i)
        body.push_back(w.tileOf(8 - i, 8));
    test::body(w, body);
    int const distance = P.ownRamSteps - 1;
    int const dx = distance / 2, dy = distance - dx;
    for (int t = 0; t < w.N; ++t)
        if (std::abs(w.xOf(t) - 8) > 3 || std::abs(w.yOf(t) - 8) > 3)
            w.visStamp[t] = 0;
    test::dragon(w, 2, true, w.tileOf(8 + dx, 8 + dy), w.length + P.outnumberedAttackMinGain);
    auto const ram = ramFor(w);
    test::require(ram.steps.size() == distance,
                  "a diagonal head inside normal vision can need more than the legacy sprint cap");
    int at = w.head;
    for (int dir : ram.steps)
        at = w.step(at, dir);
    test::require(at == w.dragons.back().head, "the sprint ends at the visible enemy head");
}

void tradeAndEnemyModelStayTheSame() {
    auto w = corridor(P.ownRamSteps + 1, P.ownRamSteps - 1);
    w.dragons.back().visibleLen = w.length - 1;
    test::require(ramFor(w).steps.empty(), "extra reach does not justify a shorter victim");
    test::require(bot::ramReach(P.ownRamSteps + 1) == P.maxRamSteps,
                  "the legacy enemy sprint model retains its existing cap");
    test::require(bot::ownRamReach(P.ownRamSteps + 1) == P.ownRamSteps,
                  "our close-in reach uses the extended cap");
}

void hunterLeavesReachableHeadToRam() {
    int const length = std::min(P.hunterMaxLength, P.ownRamSteps + 1);
    auto w = corridor(length, length - 1);
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    bot::DistField field;
    test::require(!bot::findRam(w, safety).steps.empty(), "the hunter can already ram the head");
    test::require(bot::huntDirection(w, safety, bot::evaluateMoves(w, safety, targets), field) < 0,
                  "a reachable head does not trigger another approach step");
}

} // namespace

int main() {
    return test::suite({
        {"long sprint length and cap", longSprintAndLengthBoundary},
        {"long sprint blocked or blind", blockedAndUnseenPaths},
        {"long sprint within normal vision", diagonalHeadInsideNormalVision},
        {"long sprint retains trade and enemy model", tradeAndEnemyModelStayTheSame},
        {"hunter yields to extended ram", hunterLeavesReachableHeadToRam},
    });
}
