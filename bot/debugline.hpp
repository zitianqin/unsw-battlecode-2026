#pragma once

#include "census.hpp"
#include "infer.hpp"
#include "feed.hpp"
#include "notes.hpp"
#include "params.hpp"
#include "ram.hpp"
#include "rammodel.hpp"
#include "rooms.hpp"
#include "safety.hpp"
#include "scoring.hpp"
#include "state.hpp"
#include "targets.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <sstream>
#include <string>
#include <string_view>

namespace bot {

// Notes the ram main plays this turn for debugLine: its first step, its steps, its predicted swing and the head it
// ends on.
inline void noteRam(World const& w, Safety const& safety, Ram const& ram) {
    notes.firstStep = ram.steps.front();
    notes.ramSteps = static_cast<int>(ram.steps.size());
    notes.ramVictimLen = ram.victimLen;
    notes.ramPred = ramSwing(w, safety, ram.victim, ram.victimLen);
    notes.ramVictim = w.head;
    for (int dir : ram.steps)
        notes.ramVictim = w.step(notes.ramVictim, dir);
}

// What main decided this turn, for debugLine.
struct TurnLog {
    char const* rule = "score"; // the rule that decided the turn
    bool split = false;         // the turn was a split (threatshed, splitout, shed or split)
    bool stepped = false;       // a single step to the best-scored (or hunt) move
    bool fed = false;           // feedDirection or capFeedDirection decided the turn
    bool hunt = false;          // huntDirection chose the move
    bool seeking = false;       // seekFeeder retargeted us to a feeder
    bool approaching = false;   // approachKing retargeted us to the king's head (capfeed.hpp)
    bool king = false;          // isKing
    bool kingHolds = false;     // kingHoldsSplits: only split-outs were asked for
    Fold fold;
    RoomMap const* rooms = nullptr; // the room map, current for this turn's edges (rooms.hpp); null leaves out its keys
    CensusTable const* census = nullptr; // the team's census records (census.hpp); null leaves out its keys
    SenderSet const* senders = nullptr;  // where the senders of this turn's unreadable values can be (infer.hpp); null leaves out its key
    int filed = 0;                  // decoded enemy heads filed in the belief map this turn (Belief::noteEnemyHead)
    bool shedLong = false;          // this dragon is long enough to shed under threat (split.hpp's shedPays)
    int threat = -1;                // shedThreat's enemy id for a dragon with shedLong (split.hpp), -1 none
    char const* noshed = nullptr;   // shedRefusal's reason a threatened dragon didn't shed (split.hpp)
};

// The debug log line's text (main writes it with P.debugIndicator on, which only adds this line; the loop's rule counts
// tally it). Pairs of key and value; the words are tallied by value, the numbers by band (loop/rulecounts.py):
// - r, len, body, dir (the best-scored move, `move`), score, space, target (the pearl target), rule; farm (we stepped
//   into a pocket farmEntryOk accepted), king, fold (the king we walk to, else -1), feed (a merge death decided the
//   turn); alt (the runner-up, runnerUp), gap (the score difference to it), term (decidingTerm), and pen
//   (decidingPenalty: short, unseen, risk, friend, seal, cut or search, for a fixed score lethal, door, portal,
//   headon or ram, else fixed or none) when term is penalty; tgap, when `move` and alt are both steps, the target or
//   explore term's difference to alt in path steps (positive: `move` gains on alt); units, our team's unit count.
// - tstep (1 when `move` lowers our pearl or explore field distance) with a target or explore field; when it doesn't
//   and some move does, pterm (decidingTerm between `move` and the best-scored move that does, counting only parts in
//   `move`'s favour) and ppen (likewise its decidingPenalty when pterm is penalty): what turned the dragon off its
//   target, a one-step pearl included.
// - edge: Safety::headEdge, every turn.
// - threat, for a dragon long enough to shed (TurnLog::shedLong): the id of the first enemy head shedLength's test judges able to
//   ram us (shedThreat, run whatever the round; -1 none), and with one, unless the turn shed, noshed (shedRefusal: ram,
//   escape, off, round, slot, tail, exit or none).
// - cap (at the unit limit), cut (moves given P.headReachPenalty), cutflip (the best move without that penalty isn't
//   `move`), doom (the best score is at most P.doomedScore), closed (`move` is a closed pocket), blind (its
//   destination is out of sight: a portal landing). `move` is the line's dir: the best-scored or hunt move, which on
//   ram, escape, feed and split turns is not what was played.
// - door (a mask of the sides whose crossing a teammate's doorway probe refused, doorway.hpp) and doorflip (1 when
//   one of those crossings, scored by evaluateMove as if no probe came, beat every other move; approximate, since
//   that score lacks evaluateMoves' later cut-off, search and seal penalties) on turns a probe refused a crossing.
// - freed (none, own, other or both: whose vacating tiles `move`'s decisive fill entered, nofill when evaluateMove
//   ran none) and fedge (unseen when that fill crossed an edge we have never seen, else known).
// - tk, the target's kind: visible, drop, remembered, team, fast, probable, spawn (Targets::consider), fight
//   (Targets::addFights), king (a fold's, or a cap feeder's walk: capfeed.hpp's approachKing),
//   feeder (seekFeeder's), race, roomexit (targets.hpp's roomExitWins: through an unknown portal out of a
//   portal-only room), jamexit (jamLeaveWins: out of a jammed room, jamroom.hpp), portal (an explore target through an unknown portal), explore or none; tready
//   (pearlReady) with a target; tref (1 when a pearl target one step away wasn't where the turn stepped) when it is
//   one step away, on turns that stepped or sprinted (not splits); tdrop 1 when the move scorer refused the turn's first pearl target and this is its replacement
//   (refuse.hpp); xnew, on a turn chooseExplore picked afresh, the pick's path steps (-1 none), and xalt/xaltp when
//   the other P.portalFrontier rule would have picked otherwise: its pick's path steps and whether it is a portal;
//   xpp, the steps to the unknown portal that beats the turn's pearl target (Targets::portalOverPearl; with
//   P.portalFrontier, whether P.portalBeatsPearl is on or off); jam heads/load/free when our room is jammed
//   (jamroom.hpp) and jleave, our steps to its exit when we are its leaver (-1 not), with P.leaveJam on or off;
//   xg, beside an unknown portal edge before P.crossModelRounds, the leaders' crossing model's chance in thousandths,
//   and xgd the edge's direction P.crossGate crosses (or would, off), -1 none (Targets::crossGateWins);
//   ty, why choosePearl has no pearl target: target (it has one), none (no pearl in view), far (also a pearl in view
//   that never became a candidate), slack, refused, home, jam (in a jammed room we left, jamroom.hpp's bar), mate, tie
//   or enemy (Targets::choosePearl); bna and bnb, while contested ground is on with P.bandNumbers, our heads on or
//   heading to the band and theirs (contested.hpp); bchg 1 when choosePearl would pick another target without I1118's
//   parts (Targets::bandShadow; absent otherwise and on tdrop turns); bha, bhb and bho, while the race runs with
//   P.bandContested, our heads on the band, theirs seen or heard (ours until the band is observed) and whether it is
//   (race.hpp BandHeads); cchg 1 when choosePearl would pick another target with the band commit unconditional
//   (Targets::commitShadow).
// - reach (this turn's pearl reach, Targets::reachSteps), sreach (the reach with P.rateReach flipped), rchg (1 when
//   choosePearl would pick another target under sreach; 0 on tdrop turns, whose shadow was picked before the refusal)
//   and with it skind, sdist (that target's kind and torus distance, none and -1 for no target) and tdist (our target's
//   path, -1 with none or a fold's or feeder's); xep, xst and xopen (Targets::ExploreRate: finished explore episodes,
//   their turns and the open one's, counted up to the last turn).
// - own: full (the tracked body is our whole length), born (never full since this process started) or reset (full
//   once, then trackOwnBody rebuilt it short from vision); sym: known or none (World::symmetry).
// - nosplit: none when we split, short below P.splitAtLength, else what held the ordinary split back: the rule that
//   took the turn (ram, escape, feed, capfeed, hunt), fold, king (kingHoldsSplits) or shouldSplit's gate (seed: a split
//   every gate allowed, held by a seeding king, fold.hpp's kingSeedHolds); roomheld (split or shed) when
//   roomsplit.hpp's closed-room gate refused that split this turn.
// - gate, from P.foldRound (or foldStart, if earlier): why a dragon isn't folding (foldTowardKing's gate), or for a folder feedDirection's
//   answer (merge, dropwin when the drop race let it through, or its gate), or the rule that took the turn before it was asked.
// - capfeed: capFeedDirection's answer (fire, walk or its gate) on turns it was asked (no fold), capg (our intake rate
//   in pearls per 1000 rounds) once it read it, and capbelow (unit slots under the ceiling) when there are any.
// - On ram turns, vgain (the victim's visible length less ours), rsteps (the sprint's steps), redge (our heads
//   less theirs within P.dropEdgeRadius of the victim, the two dying heads left out: who contests the drop) and rpred
//   (the ram's predicted swing, ramSwing, x 100).
// - On feed turns, fking (the teammate feedDirection merges into), fmate and fothers (its path steps and the nearest
//   other head's, -1 unmeasured); on seekFeeder turns, seek (the teammate id targeted).
// - With the room map: room (our head's room id in rooms.hpp's RoomMap, which renumbers when edges change), rtiles
//   (its tiles), rexits (doors that lead somewhere/known portals/unknown portals, e.g. 1/2/0) and rportal (1 when
//   every exit of the room's area is a portal: RoomMap::portalOnly; a thin dead end reads the room it opens onto).
// - With P.teamCensus, the team's census estimate (CensusTable::estimate, our own counts included): census (short enemy
//   heads per dragon-turn x 1000, -1 with no counts), csend (the senders whose counts it rests on, us included), clong
//   (the longest enemy head they saw, visible segments), cids (distinct enemy ids the team saw this epoch and the last,
//   rounded) and cobs (about how many of our dragons saw them).
// - With the sender set: infer (values heard this turn that failed our check) and itiles (the tiles their senders can
//   be on, infer.hpp; 0 with none heard or P.inferSender off), and lfiled (decoded enemy heads filed in the belief map
//   this turn, P.listenToBelief).
// - fs: the final fold's first round (foldStart).
inline std::string debugLine(World const& w, Safety const& safety, Targets const& targets,
                             std::array<MoveEval, 4> const& moves, MoveEval const& move, TurnLog const& turn) {
    std::ostringstream line;
    MoveEval const& alt = runnerUp(moves, move.dir);
    line << "r " << w.round << " len " << w.length << " body " << w.body.size() << " dir " << DIR_CHAR[move.dir]
         << " score " << move.score << " space " << move.space << " target " << targets.pearl << " rule " << turn.rule
         << " farm " << int(turn.stepped && move.farm) << " king " << int(turn.king) << " fold "
         << (turn.fold.active ? turn.fold.kingId : -1) << " feed " << int(turn.fed) << " alt " << DIR_CHAR[alt.dir]
         << " gap " << move.score - alt.score << " term " << decidingTerm(move, alt);
    if (std::string_view(decidingTerm(move, alt)) == "penalty")
        line << " pen " << decidingPenalty(move, alt);
    double const progressWeight = targets.pearl >= 0 ? P.targetWeight : P.exploreWeight;
    double const progressGap = move.terms[TERM_TARGET] + move.terms[TERM_EXPLORE] - alt.terms[TERM_TARGET] -
                               alt.terms[TERM_EXPLORE];
    if (move.step && alt.step && progressWeight > 0)
        line << " tgap " << std::lround(progressGap / progressWeight);
    line << " units " << w.unitCount;
    if (DistField const* field = progressField(targets)) {
        auto const lowers = [&](MoveEval const& m) { return m.dest >= 0 && field->at(m.dest) < field->at(w.head); };
        MoveEval const* ahead = nullptr;
        for (auto const& m : moves)
            if (lowers(m) && (!ahead || m.score > ahead->score))
                ahead = &m;
        line << " tstep " << int(lowers(move));
        if (!lowers(move) && ahead) {
            char const* const pterm = decidingTerm(move, *ahead, true);
            line << " pterm " << pterm;
            if (std::string_view(pterm) == "penalty")
                line << " ppen " << decidingPenalty(move, *ahead, true);
        }
    }

    line << " edge " << safety.headEdge;
    if (turn.shedLong) {
        line << " threat " << turn.threat;
        if (turn.noshed)
            line << " noshed " << turn.noshed;
    }

    bool const penalised = std::any_of(moves.begin(), moves.end(), [](MoveEval const& m) { return m.open && !m.cutOff; });
    int cut = 0;
    std::array<MoveEval, 4> unpenalised = moves;
    for (auto& m : unpenalised) {
        if (penalised && m.cutOff) {
            cut++;
            m.score += P.headReachPenalty;
        }
    }
    int const heading = w.facing[w.head];
    bool const cutflip = bestOf(unpenalised, heading).dir != move.dir;
    bool const blind = move.dest == UNKNOWN_PORTAL || (move.dest >= 0 && !w.visible(move.dest));
    line << " cap " << int(w.unitCount >= w.unitLimit) << " cut " << cut << " cutflip " << int(cutflip) << " doom "
         << int(bestOf(moves, heading).score <= P.doomedScore) << " closed " << int(move.closed) << " blind "
         << int(blind);

    int doorMask = 0;
    double probed = P.lethalScore * 10, rest = P.lethalScore * 10;
    for (int dir = 0; dir < 4; dir++) {
        if (w.doorProbed[dir]) {
            doorMask |= 1 << dir;
            probed = std::max(probed, notes.doorUnprobed[dir]);
        } else {
            rest = std::max(rest, moves[dir].score);
        }
    }
    if (doorMask)
        line << " door " << doorMask << " doorflip " << int(probed > rest);

    FillNotes const& fill = notes.moveFill[move.dir];
    char const* freed = !fill.ran                          ? "nofill"
                        : fill.freedOwn && fill.freedOther ? "both"
                        : fill.freedOwn                    ? "own"
                        : fill.freedOther                  ? "other"
                                                           : "none";
    line << " freed " << freed << " fedge " << (!fill.ran ? "nofill" : fill.unseenEdge ? "unseen" : "known");

    bool const chosen = targets.pearl >= 0 && targets.pearl == notes.chosenPearl;
    char const* kind = turn.seeking                                   ? "feeder"
                       : targets.pearl >= 0 && !chosen && (turn.fold.active || turn.approaching) ? "king"
                       : chosen && notes.pearlKind                    ? notes.pearlKind
                       : targets.pearl >= 0                           ? "other"
                       : targets.explore < 0                          ? "none"
                       : targets.exploreFromRace                      ? "race"
                       : targets.exploreFromRoom                      ? "roomexit"
                       : targets.exploreFromJam                       ? "jamexit"
                       : targets.explorePortalDir >= 0                ? "portal"
                                                                      : "explore";
    line << " tk " << kind;
    if (notes.exploreShadow != -1) {
        line << " xnew " << notes.exploreDist;
        if (notes.exploreShadow >= 0)
            line << " xalt " << notes.exploreShadowDist << " xaltp " << int(notes.exploreShadowPortal);
    }
    if (notes.portalOverPearl >= 0)
        line << " xpp " << notes.portalOverPearl;
    if (targets.jamLeave.heads > 0 && targets.jamLeave.load > targets.jamLeave.free)
        line << " jam " << targets.jamLeave.heads << '/' << targets.jamLeave.load << '/' << targets.jamLeave.free
             << " jleave " << notes.jamLeave;
    if (notes.crossChance >= 0)
        line << " xg " << std::lround(1000 * notes.crossChance) << " xgd " << notes.crossDir;
    if (notes.dropped)
        line << " tdrop 1";
    if (targets.pearl >= 0)
        line << " tready " << targets.pearlReady;
    if (chosen && notes.firstStep >= 0) {
        for (int dir = 0; dir < 4; dir++)
            if (w.step(w.head, dir) == targets.pearl)
                line << " tref " << int(notes.firstStep != dir);
    }
    char const* why = notes.chosenPearl >= 0 ? "target" : notes.noPearl;
    for (int t = 0; !why && t < w.N; t++)
        if (w.visible(t) && w.pearlNow[t] && t != w.head)
            why = "far";
    line << " ty " << (why ? why : "none");
    if (targets.contested.on && P.bandNumbers)
        line << " bna " << targets.contested.heads << " bnb " << targets.contested.enemyHeads;
    if (targets.bandShadow != notes.chosenPearl && !notes.dropped)
        line << " bchg 1";
    if (targets.race >= 0 && P.bandCommit && P.bandContested)
        line << " bha " << targets.bandHeads.ours << " bhb " << targets.bandHeads.theirs << " bho "
             << int(targets.bandHeads.observed);
    if (targets.commitShadow != notes.chosenPearl && !notes.dropped)
        line << " cchg 1";
    bool const reachChanged = !notes.dropped && targets.shadowPearl != notes.chosenPearl;
    line << " reach " << targets.reach << " sreach " << targets.shadowReach << " rchg " << int(reachChanged) << " xep "
         << targets.exploreRate.episodes << " xst " << targets.exploreRate.steps << " xopen " << targets.exploreRate.open;
    if (reachChanged) {
        char const* shadowKind = targets.shadowPearl >= 0 ? kindAt()[targets.shadowPearl] : nullptr;
        line << " skind " << (shadowKind ? shadowKind : "none") << " sdist "
             << (targets.shadowPearl >= 0 ? w.torusDist(w.head, targets.shadowPearl) : -1) << " tdist "
             << (chosen ? targets.pearlField.at(w.head) : -1);
    }

    if (notes.enemyTeam >= 0)
        line << " eteam " << notes.enemyTeam << " fakes " << notes.fakes << " lrec " << int(notes.listenRecord);

    bool const full = static_cast<int>(w.body.size()) >= w.length;
    line << " own " << (full ? "full" : notes.everFull ? "reset" : "born") << " sym "
         << (w.symmetry >= 0 ? "known" : "none");

    char const* nosplit = turn.split                   ? "none"
                          : w.length < P.splitAtLength ? "short"
                          : turn.stepped && turn.hunt  ? "hunt"
                          : !turn.stepped              ? turn.rule
                          : turn.fold.active           ? "fold"
                          : turn.kingHolds             ? "king"
                          : notes.splitHeld            ? notes.splitHeld
                                                       : "unknown";
    line << " nosplit " << nosplit;
    if (notes.roomHeld)
        line << " roomheld " << (notes.roomHeld == notes.splitHeld ? "split" : notes.roomHeld);
    if (notes.shedShadow)
        line << " shedshadow " << notes.shedShadow;

    if (w.round >= std::min(P.foldRound, foldStart())) {
        char const* gate = !turn.fold.active ? notes.foldGate : notes.feedWhy ? notes.feedWhy : turn.rule;
        line << " gate " << (gate ? gate : "unknown");
    }

    if (notes.capFeed) {
        line << " capfeed " << notes.capFeed;
        if (notes.capGrowth >= 0)
            line << " capg " << static_cast<int>(notes.capGrowth * 1000 + 0.5);
        if (notes.capBelow > 0)
            line << " capbelow " << notes.capBelow;
    }

    if (notes.ramVictim >= 0) {
        int redge = 0;
        for (auto const& d : w.dragons)
            if (d.head >= 0 && d.head != w.head && d.head != notes.ramVictim &&
                w.torusDist(d.head, notes.ramVictim) <= P.dropEdgeRadius)
                redge += d.enemy ? -1 : 1;
        line << " vgain " << notes.ramVictimLen - w.length << " rsteps " << notes.ramSteps << " redge " << redge
             << " rpred " << std::lround(notes.ramPred * 100);
    }
    if (std::string_view(turn.rule) == "feed") {
        auto const steps = [](int d) { return d >= DistField::FAR ? -1 : d; };
        line << " fking " << notes.feedMate << " fmate " << steps(notes.feedMateDist) << " fothers "
             << steps(notes.feedOthersDist);
    }
    if (turn.seeking && targets.pearl >= 0)
        line << " seek " << w.occId[targets.pearl];
    if (turn.rooms) {
        int const room = turn.rooms->roomOf[w.head];
        RoomMap::Info const& info = turn.rooms->rooms[room];
        line << " room " << room << " rtiles " << info.tiles << " rexits " << info.doors << '/' << info.knownPortals
             << '/' << info.unknownPortals << " rportal " << int(turn.rooms->portalOnly(room));
    }
    if (turn.census && P.teamCensus) {
        CensusEstimate const est = turn.census->estimate(w.round);
        double const rate = est.shortRate();
        line << " census " << (rate < 0 ? -1 : static_cast<int>(rate * 1000 + 0.5)) << " csend " << est.senders
             << " clong " << est.longest << " cids " << static_cast<int>(est.ids + 0.5) << " cobs "
             << static_cast<int>(est.observers + 0.5);
    }
    if (turn.senders)
        line << " infer " << turn.senders->foreign << " itiles " << turn.senders->tiles.size() << " lfiled "
             << turn.filed;
    line << " fs " << foldStart();
    return line.str();
}

} // namespace bot
