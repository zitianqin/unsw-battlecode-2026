// Bot v1: the split-at-4 swarm with timing-aware survival. Each dragon runs this loop in its own process:
// observe (state.hpp), note portal landings we watch (landing.hpp: they price crossing an unknown portal) and merge
// teammates' reports into the team belief map (belief.hpp), count the enemy heads in view for the team's census of
// the opponent (census.hpp), find the map's
// symmetry and write what we have seen into its mirror half (symmetry.hpp), pick a target
// (targets.hpp; in a room only portals leave, the round-0 dragon nearest an unknown portal out of it crosses, roomexit.hpp;
// in a room holding more of our heads than it can carry, the one nearest an exit walks out, jamroom.hpp;
// in the opening, beside an unknown portal a dragon crosses where a model of the top three teams says they would,
// crossgate.hpp;
// late in the game a king heads for teammates that would merge into it, feed.hpp, and donors within foldPathCap
// head for the king reported over sonar, fold.hpp), ram an enemy head we can reach this turn (ram.hpp), sprint two
// steps out of a bad rammer's reach when every single step is in it (escape.hpp),
// shed all but a length-2 front when a shorter enemy head could sprint-ram us (split.hpp's shedLength; in the
// fold as its threatShedOn allows),
// late in the game die beside a teammate at least as long so it eats our pearls (feed.hpp; a
// doomed dragon that can split its tail out does that instead), or die beside the king when that gives it more than
// we would bring it alive, below the unit cap (while the king seed runs) counting the unit our death leaves
// empty, walking to the king first when it is too far (capfeed.hpp), else score the four
// moves (scoring.hpp, safety.hpp; a tie-break keeps portal doorways clear, doorway.hpp; a short search, search.hpp, charges a move after which an enemy head near us whose
// ram would be a bad trade can force one, sprints included; when they refuse every approach to the pearl target, drop that target, pick
// another and score them again: refuse.hpp): a short dragon closes in on a longer (or even, when we'd eat its drop) enemy head it will ram (hunt.hpp),
// otherwise it splits instead when the split policy allows it (split.hpp; a king holds its ordinary splits and
// farm trips in the fold, and while a child is worth less to it than it takes: fold.hpp's kingSeedOn)
// or plays the best move; after a move or split, gossip the belief map's fresh reports over sonar, using the neck
// ray for symmetry on split turns (gossip.hpp), or a wall block on a proven child ray (kelp.hpp); countdown spare
// bits also carry walls; a head on a portal mouth sends a doorway probe through each portal edge instead, and a probe
// heard for the edge beside our head makes that crossing score as a head-on with a teammate (doorway.hpp). Heard
// values our wire check refuses are the enemy's: the listener names their team from them (listen.hpp), gossip shares
// it (a split child gets it on its first turn), and fakes that pass that team's check go on the rays gossip leaves
// (spoof.hpp). Enemy heads their reports decode to, and blocks the senders of values we can't read are likely in
// (infer.hpp), go into the belief map, so gossip relays them. With P.debugIndicator each turn logs one line for the reviews' rule counts (debugline.hpp):
// the best-scored move, the rule that decided the turn (ram, escape, threatshed, feed, capfeed, splitout, shed, split,
// hunt, fold, capwalk, feeder or score), what that move beat, why the turn's gates went the way they did (notes.hpp), and our
// room and its exits (rooms.hpp), and the team's census estimate.
// All tunables live in params.hpp.
#include "belief.hpp"
#include "capfeed.hpp"
#include "census.hpp"
#include "claims.hpp"
#include "debugline.hpp"
#include "doorway.hpp"
#include "escape.hpp"
#include "feed.hpp"
#include "fold.hpp"
#include "gossip.hpp"
#include "helper.hpp"
#include "hunt.hpp"
#include "infer.hpp"
#include "listen.hpp"
#include "params.hpp"
#include "ram.hpp"
#include "refuse.hpp"
#include "rooms.hpp"
#include "safety.hpp"
#include "scoring.hpp"
#include "split.hpp"
#include "spoof.hpp"
#include "symmetry.hpp"
#include "state.hpp"
#include "targets.hpp"

#include <string>
#include <string_view>
#include <vector>

int main() {
    try {
        auto [ct, game] = unswbc::init();
        bot::World world;
        world.init(ct, game);
        bot::Safety safety;
        bot::Targets targets;
        bot::DistField feedField;
        bot::DistField huntField;
        bot::DistField foldField;
        bot::DistField shedField;
        bot::DistField capField;
        bot::Intake intake;
        bot::Belief belief;
        belief.init(world);
        targets.team = &belief;
        bot::Landings landings;
        targets.landings = &landings;
        bot::Claims claims;
        targets.claims = &claims;
        bot::CrossGate crossGate;
        targets.crossGate = &crossGate;
        bot::Gossip gossip;
        bot::listen::Listener listener;
        bot::spoof::Spoofer spoofer; // fakes in the enemy team's own format on the rays gossip leaves (P.spoof)
        bot::Census census;
        bot::RoomMap rooms; // for the room exit and the jammed room's (targets.hpp's roomExitWins, jamLeaveWins) and the debug line (rooms.hpp)
        targets.rooms = &rooms;

        while (unswbc::update(ct, game)) {
            world.observe(ct, game);
            intake.observe(world);
            bot::teamTrend = intake.unitTrend(world.round, unswbc::Constants::MAX_ROUNDS - world.round);
            landings.observe(world);
            if constexpr (bot::kNotes)
                bot::notes.clear();
            belief.observe(world);
            auto const& raw = ct.get_sonar_messages();
            auto const heard = bot::wire::openAll(raw, world.round, belief.context());
            bot::SenderSet const senders = bot::inferSenders(world, static_cast<int>(raw.size() - heard.size()));
            if (P.listen) {
                listener.hear(world, belief.context(), ct.get_sonar_messages());
                auto const known = listener.known();
                if (P.spoof) {
                    spoofer.hear(ct.get_sonar_messages(), world.round, belief.context());
                    spoofer.format = known.format;
                }
                gossip.detection = P.listenShare ? known : bot::listen::Detection{};
            }
            belief.hear(world, heard);
            int filed = 0; // decoded enemy heads filed in the belief map this turn (debug line key `lfiled`)
            if (P.listen && P.listenToBelief && listener.followed >= 0)
                for (auto const& f : listener.fresh)
                    if (!f.sighting && !bot::spoof::ours(f, world.W, world.N, belief.context()))
                        filed += belief.noteEnemyHead(f.y * world.W + f.x, f.round, world.round);
            belief.noteSenders(senders.tiles, world.round);
            bot::hearProbes(world, belief.context(), heard);
            if (P.pearlClaims)
                claims.hear(world, belief.context(), heard);
            crossGate.observe(world, belief);
            census.observe(world, belief.census);
            bot::readSymmetry(world, belief);
            gossip.hearWalls(world, belief, heard);
            bot::startFold(world);
            bot::reportKing(world, belief);
            safety.prepare(world);
            if (P.portalRoomExit || P.leaveJam || bot::kNotes)
                rooms.update(world);
            targets.king = bot::isKing(world, belief);
            targets.update(world, !intake.eaten.empty() && intake.eaten.back() > 0);
            bool const mayFarm = !bot::kingSkipsFarms(world, belief);
            bot::Fold fold = bot::foldTowardKing(world, belief, targets, foldField);
            bool const foldKing = bot::kingSeeksInFold(world, belief);
            // Asked with the debug line on even while P.capFeed is off, so a logging copy counts where it would fire.
            auto const askCapFeed = [&] {
                return fold.active || !(P.capFeed || bot::kNotes)
                           ? bot::CapFeed{}
                           : bot::capFeedDirection(world, belief, intake, capField);
            };
            bot::CapFeed capFeed = askCapFeed();
            bool approaching = !fold.active && bot::approachKing(world, capFeed, targets, capField);
            bool seeking = !fold.active && !approaching && bot::seekFeeder(world, safety, targets, feedField, foldKing);
            auto moves = bot::evaluateMoves(world, safety, targets, mayFarm);
            if (bot::dropRefusedTarget(world, targets, moves)) {
                fold = bot::foldTowardKing(world, belief, targets, foldField);
                capFeed = askCapFeed();
                approaching = !fold.active && bot::approachKing(world, capFeed, targets, capField);
                seeking = !fold.active && !approaching && bot::seekFeeder(world, safety, targets, feedField, foldKing);
                moves = bot::evaluateMoves(world, safety, targets, mayFarm);
                if constexpr (bot::kNotes)
                    bot::notes.dropped = true;
            }
            bool const seedHolds = bot::kingSeedHolds(world, belief);
            int const hunt = bot::huntDirection(world, safety, moves, huntField);
            bot::MoveEval const move = hunt >= 0 ? moves[hunt] : bot::bestOf(moves, world.facing[world.head]);
            gossip.claim.reset();
            if (P.pearlClaims && hunt < 0 && targets.pearl >= 0 && targets.pearl == targets.picked &&
                !bot::Targets::enemyHeadAt(world, targets.pearl))
                gossip.claim = bot::PearlClaim{
                    .eat = world.round + std::max(targets.pearlField.at(world.head), targets.pearlReady),
                    .tile = targets.pearl,
                    .id = world.myId};

            // The rule that decided this turn, for the debug log line only (the reviews' rule counts tally it).
            char const* rule = "score";
            bool stepped = false; // a single step to the best-scored (or hunt) move
            bool fed = false;     // feedDirection (or capFeedDirection) decided the turn
            bool capFed = false;  // capFeedDirection decided it
            bool split = false;   // the turn split
            if (bot::Ram const ram = bot::findRam(world, safety); !ram.steps.empty()) {
                rule = "ram";
                std::vector<unswbc::Direction> steps;
                for (int dir : ram.steps)
                    steps.emplace_back(bot::DIR_CHAR[dir]);
                ct.make_moves(steps);
                intake.spent(static_cast<int>(ram.steps.size()) - 1);
                if constexpr (bot::kNotes)
                    bot::noteRam(world, safety, ram);
            } else if (bot::Escape const escape = bot::findEscape(world, safety); !escape.steps.empty()) {
                rule = "escape";
                if constexpr (bot::kNotes)
                    bot::notes.firstStep = escape.steps.front();
                std::vector<unswbc::Direction> steps;
                int at = world.head;
                for (int dir : escape.steps) {
                    steps.emplace_back(bot::DIR_CHAR[dir]);
                    at = world.step(at, dir);
                    belief.noteEaten(at);
                }
                ct.make_moves(steps);
                intake.spent(static_cast<int>(escape.steps.size()) - 1);
                gossip.claim.reset(); // an escape leaves the pearl target behind
                gossip.plan(world, belief, at, (escape.steps.back() + 2) % 4);
                spoofer.plan(world, at, (escape.steps.back() + 2) % 4, gossip.sending);
                gossip.emit(ct, world.round, belief.context());
                spoofer.emit(ct);
            } else if (int const shed = bot::shedLength(world, ct, safety, shedField, bot::isKing(world, belief))) {
                rule = "threatshed";
                split = true;
                ct.do_split(shed);
                intake.spent(shed);
                gossip.claim.reset(); // so does a threat shed
                gossip.plan(world, belief, world.head, bot::neckDir(world), true, shed);
                spoofer.plan(world, world.head, bot::neckDir(world), gossip.sending);
                gossip.emit(ct, world.round, belief.context());
                spoofer.emit(ct);
            } else if (int const feed = [&] {
                           if (bot::splitOutLength(world, ct, move))
                               return -1;
                           int const merge = bot::feedDirection(world, feedField, fold);
                           capFed = merge < 0 && P.capFeed && capFeed.dir >= 0;
                           return capFed ? capFeed.dir : merge;
                       }();
                       feed >= 0) {
                rule = capFed ? "capfeed" : "feed";
                fed = true;
                if (feed == bot::CapFeed::DIE_IN_PLACE) {
                    ct.do_split(1); // an illegal split (child under MIN_SIZE): the engine kills us where we stand
                } else {
                    if constexpr (bot::kNotes)
                        bot::notes.firstStep = feed;
                    ct.make_move(unswbc::Direction(bot::DIR_CHAR[feed]));
                }
            } else if (char const* splitRule = "splitout";
                       int const child =
                           hunt >= 0 ? 0
                           : fold.active || bot::kingHoldsSplits(world, belief)
                               ? bot::splitOutLength(world, ct, move)
                               : bot::shouldSplit(world, ct, safety, move, &splitRule, seedHolds, intake.rate())) {
                rule = splitRule;
                split = true;
                if (std::string_view(splitRule) == "shed")
                    world.shedRound = world.round;
                ct.do_split(child);
                intake.spent(child);
                gossip.plan(world, belief, world.head, bot::neckDir(world), true, child);
                spoofer.plan(world, world.head, bot::neckDir(world), gossip.sending);
                gossip.emit(ct, world.round, belief.context());
                spoofer.emit(ct);
            } else {
                rule = hunt >= 0 ? "hunt" : fold.active ? "fold" : approaching ? "capwalk" : seeking ? "feeder" : "score";
                stepped = true;
                if constexpr (bot::kNotes)
                    bot::notes.firstStep = move.dir;
                ct.make_move(unswbc::Direction(bot::DIR_CHAR[move.dir]));
                belief.noteEaten(move.dest);
                gossip.plan(world, belief, move.dest, (move.dir + 2) % 4);
                spoofer.plan(world, move.dest, (move.dir + 2) % 4, gossip.sending);
                gossip.emit(ct, world.round, belief.context());
                spoofer.emit(ct);
            }
            if constexpr (bot::kNotes) {
                bot::notes.enemyTeam = spoofer.format >= 0 ? spoofer.team()
                                       : gossip.detection.format >= 0
                                           ? bot::listen::FORMATS[static_cast<std::size_t>(gossip.detection.format)].team
                                           : -1;
                bot::notes.fakes = spoofer.sent;
                bot::notes.listenRecord = gossip.detectionSentRound == world.round;
                bool const king = bot::isKing(world, belief);
                bool const shedLong = bot::shedPays(world);
                int const threat = shedLong ? bot::shedThreat(world, shedField) : -1;
                bot::TurnLog const log{.rule = rule,
                                       .split = split,
                                       .stepped = stepped,
                                       .fed = fed,
                                       .hunt = hunt >= 0,
                                       .seeking = seeking,
                                       .approaching = approaching,
                                       .king = king,
                                       .kingHolds = bot::kingHoldsSplits(world, belief),
                                       .fold = fold,
                                       .rooms = &rooms,
                                       .census = &belief.census,
                                       .senders = &senders,
                                       .filed = filed,
                                       .shedLong = shedLong,
                                       .threat = threat,
                                       .noshed = bot::shedRefusal(world, ct, safety, threat, king, rule)};
                rooms.update(world);
                ct.output_log(bot::debugLine(world, safety, targets, moves, move, log));
                bot::notes.everFull = bot::notes.everFull || static_cast<int>(world.body.size()) >= world.length;
            }
            unswbc::end_turn();
        }
    } catch (...) {
        // stdin closes when the game ends or the dragon dies; nothing left to do.
    }
    return 0;
}
