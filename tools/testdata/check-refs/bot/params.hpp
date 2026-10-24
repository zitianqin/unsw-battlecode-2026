#pragma once

// Lines copied from loop/champion's bot/params.hpp (1c225be).
struct Params {
    int splitAtLength = 4;       // split once the dragon is at least this long
    int childLength = 2;         // segments given to the child
    // Heading into a closed pocket with our tail trapped in another one, split our rear off (shed.hpp):
    int mergeRound = 450;
    int mergeRadius = 2;         // the teammate must reach our head tile within this many steps, before any other head
    int beaconRound = 410;       // ... from this round
    int kingMinLength = 4;       // ... when at least this long
    int foldRound = 415;         // ... from this round
    int foldMinLength = 1;       // ... when at least this long (1: every dragon)
    bool sonarSipTag = true;     // the tag is SipHash-2-4 over the body, our side and the map size, not splitmix
    bool debugIndicator = false;      // label dragons in replays (costs output bytes; keep off in the ladder)
};

inline constexpr Params P{};
