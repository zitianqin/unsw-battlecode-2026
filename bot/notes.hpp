#pragma once

#include "params.hpp"

#include <array>
#include <type_traits>
#include <vector>

// What the debug log line (debugline.hpp) needs from inside the turn's decisions, and nothing else reads: why no
// ordinary split, which fold or merge gate held a dragon back, the pearl target's kind, why a pearl in view is not our
// target, and what a move's flood fill leaned on. Every write sits under `if constexpr (kNotes)`, so with
// P.debugIndicator off (every real game) none of it is compiled and every move and CPU point is the champion's.
// Tests define BOT_NOTES true before including the bot to read the notes with the switch off.
#ifndef BOT_NOTES
#define BOT_NOTES false
#endif

namespace bot {

inline constexpr bool kNotes = P.debugIndicator || BOT_NOTES;

// A flood fill's leanings (Safety::floodFill): which bodies' vacating tiles it entered, and whether it crossed an
// edge we have never seen (assumed open).
struct FillNotes {
    bool freedOwn = false;   // entered a tile our own body is vacating
    bool freedOther = false; // entered a tile another dragon's body is vacating
    bool unseenEdge = false; // crossed an EDGE_UNKNOWN edge
    bool ran = false;        // evaluateMove ran its fills for this move (not kelp, a portal, a head or a lethal body)
};

// TERM_PENALTY's parts (scoring.hpp), for the debug log line only (decidingPenalty): a fixed score (lethal, kelp, a
// head-on, a doorway probe, an unknown portal, or the attack bonus: what the other parts leave, so never stored), the
// physical shortfall (a closed pocket or one too small), the out-of-sight tie-break (P.unseenShortfallPerTile), ram
// risk, a visible teammate's only exit, squeezing a teammate in (seal.hpp's squeeze guard; logged as seal), a head-reach cut-off and the local search's
// loss.
enum Pen { PEN_FIXED, PEN_SHORT, PEN_UNSEEN, PEN_RISK, PEN_FRIEND, PEN_SEAL, PEN_CUT, PEN_SEARCH, PEN_COUNT };
inline constexpr char const* PEN_NAME[PEN_COUNT] = {"fixed", "short", "unseen", "risk",
                                                    "friend", "seal", "cut",    "search"};

struct Notes {
    char const* splitHeld = nullptr; // shouldSplit's first gate that returned 0
    char const* roomHeld = nullptr;  // roomsplit.hpp's closed-room gate refused this turn's "split" or "shed"
    char const* shedShadow = nullptr; // shed.hpp: a trapped-tail shed the tail rules allow where P.shedStarterAnyTurn
                                      // ("starter"), P.shedEnemyByPath ("path") or both ("both") decide it
    char const* foldGate = nullptr;  // foldTowardKing's first gate that returned inactive
    char const* feedWhy = nullptr;   // feedDirection's gate that returned -1, or "merge" / "dropwin"
    char const* capFeed = nullptr;   // capFeedDirection's gate that returned -1, or "fire" or "walk" (asked even with P.capFeed off)
    double capGrowth = -1;           // ... and our intake rate, once it read it
    int capBelow = 0;                // ... and the unit slots under the ceiling where ordinary splits stop
    char const* yieldWhy = nullptr;  // someoneElseFirst's last true answer: "mate", "tie" or "enemy",
                                     //   or claimedFirst's "claim"
    char const* pearlKind = nullptr; // choosePearl's winner's kind (kindAt())
    char const* noPearl = nullptr;   // why choosePearl rejected its cheapest visible-pearl candidate
    int chosenPearl = -1;            // choosePearl's winner, before a fold or seekFeeder retargets
    int firstStep = -1;              // main: the direction of the turn's (first) step, -1 on a split
    int ramVictim = -1;              // main: the tile of the enemy head a ram ends on, -1 no ram ...
    int ramVictimLen = 0;            // ... its visible length
    int ramSteps = 0;                // ... and the sprint's steps
    double ramPred = 0;              // ... and its predicted swing (ramSwing)
    int feedMate = -1;               // feedDirection: the teammate nearest by path it would merge into (-1 none) ...
    int feedMateDist = -1;           // ... its path steps (-1 not measured)
    int feedOthersDist = -1;         // ... and the nearest other head's (-1 not measured)
    bool dropped = false;            // main: the pearl target was refused and replaced this turn (refuse.hpp)
    int exploreShadow = -1;          // chooseExplore picked afresh this turn: the pick the other P.portalFrontier rule
                                     // would have made (-2 the same pick), -1 no fresh pick ...
    bool exploreShadowPortal = false; // ... whether that pick was an unknown portal
    int exploreShadowDist = -1;      // ... its path steps (a portal's one past its tile)
    int exploreDist = -1;            // ... and the path steps of the pick made
    int portalOverPearl = -1;        // steps to the unknown portal that beats this turn's pearl target (-1 none), with
                                     // P.portalBeatsPearl on or off (Targets::portalOverPearl)
    int jamLeave = -1;               // steps to the exit this dragon would take out of its jammed room (jamroom.hpp),
                                     // with P.leaveJam on or off, -1 not the leaver
    int enemyTeam = -1;              // main: the enemy team the team knows of (listen.hpp, spoof.hpp), -1 none ...
    int fakes = 0;                   // ... fakes sent this turn (spoof.hpp) ...
    bool listenRecord = false;       // ... and whether our gossip carried the listen record this turn
    double crossChance = -1;         // the leaders' crossing model's chance beside an unknown portal (-1 none) ...
    int crossDir = -1;               // ... and the edge P.crossGate crosses (or would, with it off), -1 none
                                     // (Targets::crossGateWins)
    bool everFull = false;           // main: our tracked body has been our whole length on some turn of this process
    std::array<double, 4> doorUnprobed{}; // by direction: evaluateMove's score for a crossing a doorway probe refused,
                                          // as if no probe came (doorway.hpp)
    FillNotes fill;                  // the latest floodFill's
    std::array<FillNotes, 4> moveFill{}; // by direction: evaluateMove's fill that decided `closed` (the physical one
                                         // when the first fell short of the cap)
    std::array<std::array<double, PEN_COUNT>, 4> movePens{}; // by direction: evaluateMoves' penalty parts by Pen
                                                          // (PEN_FIXED left 0: decidingPenalty derives it)
    std::array<char const*, 4> moveFixed{}; // by direction: the kind of fixed score evaluateMove returned for a move
                                            // that is no step: lethal (kelp, a body), door (a doorway probe), portal
                                            // (an unknown one), headon (a teammate's head) or ram (an enemy's); null
                                            // for a step

    // Call at the start of each turn (everFull carries over).
    void clear() {
        splitHeld = roomHeld = shedShadow = foldGate = feedWhy = capFeed = yieldWhy = pearlKind = noPearl = nullptr;
        capGrowth = -1;
        capBelow = 0;
        chosenPearl = firstStep = ramVictim = exploreShadow = exploreShadowDist = exploreDist = portalOverPearl = jamLeave = -1;
        exploreShadowPortal = false;
        crossChance = -1;
        crossDir = -1;
        ramVictimLen = ramSteps = 0;
        ramPred = 0;
        feedMate = feedMateDist = feedOthersDist = -1;
        dropped = false;
        enemyTeam = -1;
        fakes = 0;
        listenRecord = false;
        moveFill = {};
        movePens = {};
        moveFixed = {};
        doorUnprobed = {};
    }
};

// Constant-initialized and trivially destructible, so a build without kNotes runs no code for it.
static_assert(std::is_trivially_destructible_v<Notes>);
inline Notes notes;

// Tile -> the kind Targets::consider added it as this turn (sized by choosePearl). A function's static, so only a
// build that asks for it pays for it.
inline std::vector<char const*>& kindAt() {
    static std::vector<char const*> kinds;
    return kinds;
}

} // namespace bot
