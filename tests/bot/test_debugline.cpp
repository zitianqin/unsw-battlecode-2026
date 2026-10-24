// The debug log line's fields (debugline.hpp, notes.hpp). BOT_NOTES turns the notes on in this build only; with
// P.debugIndicator off, real games compile none of them (test_protocol.py checks the moves stay the same).
#define BOT_NOTES true

#include "support.hpp"
#include "debugline.hpp"
#include "fold.hpp"
#include "ram.hpp"
#include "scoring.hpp"
#include "split.hpp"

#include <map>
#include <string_view>
#include <sstream>

using test::require;

namespace {

// The debug line as {key: value}.
std::map<std::string, std::string> fields(std::string const& line) {
    std::istringstream in(line);
    std::map<std::string, std::string> out;
    std::string key, value;
    while (in >> key >> value)
        out[key] = value;
    return out;
}

unswbc::Controller controllerFor(bot::World const& w) {
    unswbc::Controller controller(w.myId, unswbc::Team::A, unswbc::Direction::EAST, {}, w.unitLimit);
    controller.length = w.length;
    controller.unit_count = w.unitCount;
    return controller;
}

// Our dragon (id `id`) of `length` heading east from (8,8) on test::world().
bot::World eastward(int length, int width = 16, int height = 16, int id = 1) {
    auto w = test::world(width, height);
    w.myId = id;
    std::vector<int> tiles;
    for (int i = 0; i < length; i++)
        tiles.push_back(w.tileOf(8 - i, 8));
    test::body(w, tiles);
    w.facing[w.head] = 1;
    return w;
}

// The line for a scored turn of `w`, as main builds it (notes cleared, targets and moves this turn's, and on a
// step with no hunt or fold, shouldSplit asked first).
std::map<std::string, std::string> scoredLine(bot::World const& w, bot::TurnLog turn = {.stepped = true}) {
    bot::notes.clear();
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    targets.update(w);
    auto const moves = bot::evaluateMoves(w, safety, targets);
    auto const move = bot::bestOf(moves, w.facing[w.head]);
    if (turn.stepped && !turn.hunt && !turn.fold.active)
        bot::shouldSplit(w, controllerFor(w), safety, move);
    bot::notes.firstStep = move.dir;
    return fields(bot::debugLine(w, safety, targets, moves, move, turn));
}

void ram_line_carries_edge_gain_and_steps() {
    auto w = eastward(4);
    int const victim = w.tileOf(10, 8);
    test::dragon(w, 99, true, victim, 6);
    bot::notes.clear();
    bot::Safety safety;
    safety.prepare(w);
    bot::Ram const ram = bot::findRam(w, safety);
    require(ram.steps.size() == 2 && ram.victimLen == 6, "the scenario is a two-step ram on a head 2 longer");
    bot::noteRam(w, safety, ram);
    bot::Targets targets;
    targets.update(w);
    auto const moves = bot::evaluateMoves(w, safety, targets);
    auto line = fields(bot::debugLine(w, safety, targets, moves, bot::bestOf(moves), {.rule = "ram"}));
    require(line["edge"] == "0" && line["vgain"] == "2" && line["rsteps"] == "2" && line["redge"] == "0",
            "a ram logs the head edge, the victim's gain and the sprint's steps");
    require(line["nosplit"] == "ram", "the ram took the turn from the split");

    test::dragon(w, 12, false, w.tileOf(10, 10), 2); // a teammate head 2 from the victim contests its drop
    safety.prepare(w);
    line = fields(bot::debugLine(w, safety, targets, moves, bot::bestOf(moves), {.rule = "ram"}));
    require(line["redge"] == "1" && line["edge"] == "1", "a teammate near the victim counts in redge and edge");
}

void scored_turn_carries_edge_and_no_ram_fields() {
    auto line = scoredLine(eastward(3));
    require(line["edge"] == "1" && !line.count("vgain") && !line.count("rsteps"),
            "every turn logs edge; only ram turns log vgain and rsteps");
    require(line["cap"] == "0" && line["doom"] == "0" && line["closed"] == "0" && line["blind"] == "0",
            "an open board below the cap raises no safety flag");
    require(line["nosplit"] == "short", "a dragon shorter than P.splitAtLength logs short");
}

void target_kinds_and_readiness() {
    auto w = eastward(3);
    test::pearl(w, w.tileOf(10, 8));
    auto line = scoredLine(w);
    require(line["tk"] == "visible" && line["tready"] == "0" && line["ty"] == "target",
            "a visible pearl two steps away is a visible target");
    require(!line.count("tref"), "tref is logged only for a target one step away");

    w = eastward(3);
    int const spawn = w.tileOf(10, 10);
    w.spawnKind[spawn] = bot::SPAWN_YES;
    w.spawnRound[spawn] = w.round + 3;
    line = scoredLine(w);
    require(line["tk"] == "spawn" && line["tready"] == "4" && line["ty"] == "target",
            "a countdown firing in 3 rounds 4 steps away is a spawn target ready on step 4");

    w = eastward(3);
    int const remembered = w.tileOf(10, 8);
    w.visStamp[remembered] = 0;
    w.lastSeen[remembered] = w.pearlSeen[remembered] = w.round - 1;
    line = scoredLine(w);
    require(line["tk"] == "remembered", "a pearl seen last turn out of sight is a remembered target");

    w = eastward(3);
    require(scoredLine(w)["tk"] == "none", "nothing to target and nothing unseen: no target");
    for (int t = 0; t < w.N; t++) {
        if (w.xOf(t) >= 12) { // never seen: somewhere to explore
            w.visStamp[t] = 0;
            w.lastSeen[t] = -1;
        }
    }
    line = scoredLine(w);
    require(line["tk"] == "explore" && !line.count("tready"), "no pearl anywhere: the dragon explores");
}

void refused_last_step_is_flagged() {
    auto w = eastward(3);
    test::pearl(w, w.tileOf(9, 8));
    auto line = scoredLine(w);
    require(line["tref"] == "0", "stepping onto a pearl one step away is no refusal");

    bot::notes.clear();
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    targets.update(w);
    auto const moves = bot::evaluateMoves(w, safety, targets);
    bot::notes.firstStep = -1; // a split turn: the parent stays put
    line = fields(bot::debugLine(w, safety, targets, moves, bot::bestOf(moves), {.rule = "split", .split = true}));
    require(!line.count("tref") && line["nosplit"] == "none", "a split is no step: it logs no tref");

    // The pearl ends a 1-tile dead end: the scorer turns away, and the line names the physical shortfall as what
    // refused the one-step pearl.
    w = eastward(3);
    int const pocket = w.tileOf(9, 8);
    test::pearl(w, pocket);
    for (int dir : {0, 1, 2})
        test::wall(w, pocket, dir);
    line = scoredLine(w);
    require(line["tref"] == "1" && line["tstep"] == "0", "the step into the dead end is refused");
    require(line["pterm"] == "penalty" && line["ppen"] == "short", "the closed pocket's shortfall refused it");
}

// shedLength's threat test on the line (split.hpp shedThreat, shedRefusal): the head that could ram us and, when we
// didn't shed, the gate that held it.
void threat_and_why_no_shed() {
    int const len = P.shedMinLength;
    int const theirs = len - 2;
    int const reach = (P.fullEnemyReach ? theirs - 1 : bot::ramReach(theirs)) + P.shedThreatSlack;
    auto lineWith = [&](int away, bool full) {
        auto w = eastward(len, 32, 32);
        test::dragon(w, 40, true, w.tileOf(8, 8 + away), theirs);
        w.unitCount = full ? w.unitLimit : 20;
        bot::notes.clear();
        bot::Safety safety;
        safety.prepare(w);
        bot::DistField field;
        int const threat = bot::shedThreat(w, field);
        bot::TurnLog turn{.stepped = true};
        turn.shedLong = bot::shedPays(w);
        turn.threat = threat;
        turn.noshed = bot::shedRefusal(w, controllerFor(w), safety, threat, false, "score");
        bool const sheds = bot::shedLength(w, controllerFor(w), safety, field) > 0;
        require(sheds == (turn.noshed && std::string_view(turn.noshed) == "none"),
                "shedLength sheds exactly when the log finds a threat and no gate");
        return scoredLine(w, turn);
    };
    auto line = lineWith(reach, false);
    require(line["threat"] == "40" && line["noshed"] == "none", "a head 2 shorter at its reach plus slack threatens");
    line = lineWith(reach, true);
    require(line["threat"] == "40" && line["noshed"] == "slot", "at the unit limit the shed has no slot");
    line = lineWith(reach + 1, false);
    require(line["threat"] == "-1" && !line.count("noshed"), "one step farther: no threat");
}

void why_a_pearl_in_view_is_no_target() {
    auto w = test::world();
    w.myId = 2;
    w.head = w.tileOf(4, 4);
    test::pearl(w, w.tileOf(5, 4));
    test::dragon(w, 1, false, w.tileOf(6, 4), 4);
    require(scoredLine(w)["ty"] == "tie", "a lower-id teammate as near takes the pearl on the tie");

    w.dragons[0].id = 3;
    w.occId[w.tileOf(6, 4)] = 3;
    require(scoredLine(w)["ty"] == "target", "we win the tie against a higher id: the pearl is our target");

    w = test::world();
    w.head = w.tileOf(4, 4);
    test::pearl(w, w.tileOf(7, 4));
    test::dragon(w, 9, false, w.tileOf(8, 4), 2);
    require(scoredLine(w)["ty"] == "mate", "a strictly nearer teammate takes it");

    w = test::world();
    w.myId = 2;
    w.head = w.tileOf(4, 4);
    test::pearl(w, w.tileOf(7, 4));
    test::dragon(w, 1, true, w.tileOf(8, 4), 2);
    require(scoredLine(w)["ty"] == (P.yieldToEnemyCloser ? "enemy" : "target"), "a strictly nearer enemy takes it");
}

void split_gates() {
    auto w = eastward(P.splitAtLength);
    test::dragon(w, 99, true, w.tileOf(8, 8 - P.splitEnemyHeadRadius), 2);
    bot::notes.clear();
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    auto best = bot::bestMove(w, safety, targets);
    require(!bot::shouldSplit(w, controllerFor(w), safety, best) &&
                std::string(bot::notes.splitHeld) == "enemy",
            "a long enough dragon beside an enemy head is held back by the enemy gate");

    w = eastward(P.splitAtLength);
    w.unitCount = w.unitLimit;
    bot::notes.clear();
    safety.prepare(w);
    best = bot::bestMove(w, safety, targets);
    require(!bot::shouldSplit(w, controllerFor(w), safety, best) && std::string(bot::notes.splitHeld) == "cap",
            "at the unit limit the cap holds the split");
    auto line = scoredLine(w);
    require(line["nosplit"] == "cap" && line["cap"] == "1", "the line says so");

    w = eastward(P.splitAtLength + 1);
    w.body.pop_back();
    require(scoredLine(w)["nosplit"] == "tail", "a body shorter than our length can't check the child's exit");

    w = eastward(P.splitAtLength);
    w.unitCount = 4;
    bot::notes.clear();
    safety.prepare(w);
    best = bot::bestMove(w, safety, targets);
    require(bot::shouldSplit(w, controllerFor(w), safety, best) == P.childLength && !bot::notes.splitHeld,
            "a split leaves no gate");
    require(scoredLine(w, {.rule = "split", .split = true})["nosplit"] == "none", "a split turn logs none");
    require(scoredLine(w, {.stepped = true, .hunt = true})["nosplit"] == "hunt", "a hunt's step logs hunt");
}

bot::Record kingRecord(int round, int where, int length, int id) {
    bot::Record r;
    r.king = true;
    r.round = round;
    r.where = where;
    r.length = length;
    r.id = id;
    return r;
}

char const* foldGate(bot::World const& w, bot::Belief const& b) {
    bot::notes.clear();
    bot::Targets targets;
    targets.update(w);
    bot::DistField field;
    auto const fold = bot::foldTowardKing(w, b, targets, field);
    return fold.active ? "active" : bot::notes.foldGate;
}

void fold_gates() {
    auto w = eastward(4, 32, 32, 5);
    w.round = bot::foldStart();
    w.unitCount = P.mergeMinUnits;
    bot::Belief b;
    b.init(w);
    require(std::string(foldGate(w, b)) == "floor", "a team at the merge floor doesn't fold");

    w.unitCount = 30;
    require(std::string(foldGate(w, b)) == "noking", "no king report: nobody to fold into");

    w.round = 480;
    b.merge(kingRecord(w.round - 1, w.tileOf(16, 16), 40, 2), w.round);
    require(std::string(foldGate(w, b)) == "budget", "a king 16 steps away with 15 steps left is out of time");

    b.init(w);
    b.merge(kingRecord(w.round - 1, w.tileOf(12, 8), 40, 2), w.round);
    require(std::string(foldGate(w, b)) == "active", "a king 4 steps away is folded into");

    b.init(w);
    b.merge(kingRecord(w.round - 1, w.tileOf(12, 8), 3, 2), w.round);
    require(std::string(foldGate(w, b)) == "longer", "we don't fold into a shorter king");

    auto big = eastward(4, 64, 64, 5);
    big.round = bot::foldStart();
    big.unitCount = 30;
    bot::Belief bb;
    bb.init(big);
    bb.merge(kingRecord(big.round - 1, big.tileOf(8, 8 + P.foldReach + 4), 40, 2), big.round);
    require(!P.localFold || std::string(foldGate(big, bb)) == "reach",
            "on a big map a king beyond P.foldReach with time to spare is out of reach");
}

void folder_merge_gate() {
    auto w = eastward(4, 32, 32, 5);
    w.round = P.foldRound;
    w.unitCount = 30;
    test::dragon(w, 2, false, w.tileOf(10, 8), 40);
    test::dragon(w, 99, true, w.tileOf(8, 12), 2);
    bot::Fold const fold{true, 2, 40};
    bot::DistField field;
    bot::notes.clear();
    bool const merges = P.foldIgnoresEnemy || P.foldFeedByDrop;
    require((bot::feedDirection(w, field, fold) >= 0) == merges &&
                std::string(bot::notes.feedWhy) == (P.foldIgnoresEnemy ? "merge" : merges ? "dropwin" : "enemy"),
            "a folder beside the king with an enemy head in view merges when the king wins its drop race");
    w.dragons.pop_back();
    w.occId[w.tileOf(8, 12)] = -1;
    bot::notes.clear();
    require(bot::feedDirection(w, field, fold) == 3 && std::string(bot::notes.feedWhy) == "merge",
            "without the enemy the folder dies into its own neck beside the king");

    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    auto const moves = bot::evaluateMoves(w, safety, targets);
    auto line = fields(bot::debugLine(w, safety, targets, moves, bot::bestOf(moves), {.rule = "feed", .fed = true, .fold = fold}));
    require(line["gate"] == "merge" && line["nosplit"] == "feed", "the line carries the folder's feed answer");
    bot::notes.feedWhy = nullptr; // feedDirection not asked: a ram took the turn first
    line = fields(bot::debugLine(w, safety, targets, moves, bot::bestOf(moves), {.rule = "ram", .fold = fold}));
    require(line["gate"] == "ram", "a folder whose turn another rule took logs that rule");
}

void own_body_track_and_symmetry() {
    auto w = eastward(4);
    bot::notes.everFull = false;
    require(scoredLine(w)["own"] == "full", "a whole tracked body is full");
    w.body.pop_back();
    require(scoredLine(w)["own"] == "born", "short since birth is born");
    bot::notes.everFull = true;
    require(scoredLine(w)["own"] == "reset", "short after being full is a reset");
    bot::notes.everFull = false;
    require(scoredLine(w)["sym"] == "none", "an unknown mirror is none");
    w.symmetry = 1;
    require(scoredLine(w)["sym"] == "known", "a known mirror is known");
}

void jam_penalty_count_and_flip() {
    auto w = eastward(4);
    bot::notes.clear();
    bot::Safety safety;
    safety.prepare(w);
    bot::Targets targets;
    std::array<bot::MoveEval, 4> moves{};
    for (int dir = 0; dir < 4; dir++) {
        moves[dir].dir = dir;
        moves[dir].score = P.lethalScore;
    }
    moves[0].open = moves[0].cutOff = true;
    moves[0].score = 1000 - P.headReachPenalty; // the best move without the penalty
    moves[1].open = true;
    moves[1].score = 500;
    auto line = fields(bot::debugLine(w, safety, targets, moves, moves[1], {.stepped = true}));
    require(line["cut"] == "1" && line["cutflip"] == "1", "the jam guard cut one move and changed the choice");

    moves[1].cutOff = true; // every open move can be cut off: no penalty applies
    moves[0].score = 1000;
    line = fields(bot::debugLine(w, safety, targets, moves, moves[0], {.stepped = true}));
    require(line["cut"] == "0" && line["cutflip"] == "0", "with no uncut move the guard is off");
}

void fill_notes_name_freed_tiles_and_unseen_edges() {
    auto w = eastward(4);
    bot::Safety safety;
    safety.prepare(w);
    safety.floodFill(w.tileOf(9, 8), 0, 400, {.physical = true});
    require(bot::notes.fill.freedOwn && !bot::notes.fill.freedOther && !bot::notes.fill.unseenEdge,
            "an open known board: our own tail frees, every edge is known");
    w.setEdge(w.edgeOf(w.tileOf(9, 8), 1), bot::EDGE_UNKNOWN);
    safety.floodFill(w.tileOf(9, 8), 0, 400, {.physical = true});
    require(bot::notes.fill.unseenEdge, "an unseen edge on the way is noted");

    auto line = scoredLine(eastward(4));
    require(line["freed"] == "own" && line["fedge"] == "known", "the chosen move's fill reaches our vacating tail");
}

} // namespace

int main() {
    return test::suite({
        {"ram line carries edge, victim gain and steps", ram_line_carries_edge_gain_and_steps},
        {"scored turn carries edge and no ram fields", scored_turn_carries_edge_and_no_ram_fields},
        {"target kinds and readiness", target_kinds_and_readiness},
        {"refused last step is flagged", refused_last_step_is_flagged},
        {"threat and why no shed", threat_and_why_no_shed},
        {"why a pearl in view is no target", why_a_pearl_in_view_is_no_target},
        {"split gates", split_gates},
        {"fold gates", fold_gates},
        {"folder merge gate", folder_merge_gate},
        {"own body track and symmetry", own_body_track_and_symmetry},
        {"jam penalty count and flip", jam_penalty_count_and_flip},
        {"fill notes name freed tiles and unseen edges", fill_notes_name_freed_tiles_and_unseen_edges},
    });
}
