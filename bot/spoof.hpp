#pragma once

#include "belief.hpp"
#include "helper.hpp"
#include "listen.hpp"
#include "params.hpp"
#include "state.hpp"

#include <algorithm>
#include <array>
#include <bit>
#include <cmath>
#include <cstdint>
#include <optional>
#include <utility>
#include <vector>

// Spoofing (P.spoof): once the team knows which team the enemy is (listen.hpp's detection, ours or a teammate's over
// gossip), a dragon sends fakes in that team's format on the rays our own gossip and
// doorway probes leave free, unless the ray reaches a teammate, kelp or our own body in view first. Gossip never gives
// up a ray for a fake. A fake goes out only when it passes that team's own check, so they read it as a teammate's and
// learn nothing of us; a team whose check we can't pass gets none. What each team gets:
// - a WRITERS row (a team whose consumer we measured): its own fake. Stockfish (206; no check) gets a heard report of
//   theirs with its head moved onto the target it names: their dragons keep off reported teammates out of view early
//   on (measured on their replays: away 0.48 v 0.40 placebo), so a phantom on their own targets pushes them off their
//   food.
// - any other detected team with a verified report format (P.spoofGarbage): garbage that passes their check, one of
//   their heard reports with its position moved to a tile drawn at random, the rest of its bits as heard. Their check
//   must be known: every bit is a tag or a field we decode, or the bits we don't decode were the same in every report
//   of theirs we heard, often enough that a check over the fields would not repeat by chance (checkKnown).
// - every other team (keyed or token formats: SSS's 10-bit check, Cache me outside's cipher, cheji bt): nothing.
// - decoys (P.spoofTrick), for every detected team with a verified own-head report layout: on a ray that reaches one
//   of its dragons whose head is in view, one of its heard reports with the position moved to a tile placed from that
//   dragon's head, facing, the obstacles beside it and the pearls around it (decoyTile), so it steers into an obstacle
//   or off its food. How it reacts was measured for three teams (DECOYS, ~/.bc-loop/reports/
//   decoy-reactions-2026-10-01.md); every other team's decoy works either way it reacts (Lure::Either). Other rays,
//   and rays whose board gives no lever, keep the fakes above.
// Our own code under another build's key sends values that look random, which lock no format (the bot tests' noise
// case), so games against every copy of our bot play as the champion's.
namespace bot::spoof {

// Bits [shift, shift + bits) of a value.
struct Bits {
    int shift = 0, bits = 0;
    int get(std::uint64_t v) const { return static_cast<int>(v >> shift & ((1ULL << bits) - 1)); }
    std::uint64_t put(std::uint64_t v, int value) const {
        std::uint64_t const m = ((1ULL << bits) - 1) << shift;
        return (v & ~m) | (static_cast<std::uint64_t>(value) << shift & m);
    }
};

// A team whose consumer we measured, with its own fake: `team`; a report its fake is made from is a value with
// (value & mask) == value; the fields its fake rewrites.
struct Writer {
    int team = 0;
    std::uint64_t mask = 0, value = 0;
    Bits headX, headY, targetX, targetY;
};

inline constexpr std::array WRITERS = std::to_array<Writer>({
    {.team = 206,
     .mask = 0xFULL << 60 | 1,
     .value = 0x3ULL << 60,
     .headX = {53, 7},
     .headY = {46, 7},
     .targetX = {39, 7},
     .targetY = {32, 7}},
    // Stockfish's ranked bot since 1 Oct 08:00 AEST: the same layout under top nibble 0xC (games 753265-753269 v calc;
    // its challenge games v us at 08:34 still sent 0x3), so a fake copies whichever version this game's reports show.
    {.team = 206,
     .mask = 0xFULL << 60 | 1,
     .value = 0xCULL << 60,
     .headX = {53, 7},
     .headY = {46, 7},
     .targetX = {39, 7},
     .targetY = {32, 7}},
});

// The bits of `layout` that carry a position (its x, y and tile fields).
constexpr std::uint64_t positionBits(listen::Layout const& layout) {
    std::uint64_t m = 0;
    for (listen::Field const f : {layout.x, layout.y, layout.tile})
        if (f.bits)
            m |= ((1ULL << f.bits) - 1) << f.shift;
    return m;
}

// The tile our garbage built from `value` (a report of `layout`) at `round` puts its head on: a keyed hash of the
// round and the value's bits other than its position, so to the enemy it is a random tile and to a teammate, who
// holds the key, a mark of our own fake (ours()).
inline int garbageTile(listen::Layout const& layout, std::uint64_t value, int round, int N, wire::Context const& ctx) {
    std::uint64_t const body = wire::mix((value & ~positionBits(layout)) ^ static_cast<std::uint64_t>(round) << 40);
    return static_cast<int>(wire::tagOf(body & wire::BODY_MASK, ctx) % static_cast<std::uint64_t>(N));
}

// Whether decoded fact `f` (heard at its `heard` round) is a teammate's fake rather than the enemy's report: a
// writer's fake (any of its team's WRITERS rows) has its head on its own target (a real report with the head there is
// never made into a fake), and our garbage has its head on garbageTile of the round it was sent (this round, or the
// last for a higher id's). A decoy (P.spoofTrick) goes only on a ray whose first dragon in view is the enemy's, which
// stops the ray, so no teammate hears one.
inline bool ours(listen::Fact const& f, int W, int N, wire::Context const& ctx);

// The WRITERS row for `team`, or -1.
inline int writerOf(int team) {
    for (std::size_t i = 0; i < WRITERS.size(); i++)
        if (WRITERS[i].team == team)
            return static_cast<int>(i);
    return -1;
}

// How a team's dragon reacts to a teammate's report, as measured on its replays (reports/decoy-reactions-2026-10-01:
// the move after a report heard from one side, against the same team's undecodable values from that side).
enum class Lure {
    Toward, // turns toward a teammate reported beside it: the decoy sits beside it just past an obstacle there
    Away,   // turns away from a teammate reported beside it: the decoy sits on the side with more pearls in view
    Ahead,  // holds its course after a report from ahead: the decoy sits ahead when an obstacle is there
    Either, // unmeasured (every other team): beside it on the side with an obstacle and the pearls, so turning toward
            // it hits the obstacle and turning away leaves the pearls
};

// A team whose reaction we measured (P.spoofTrick; every other team gets Lure::Either): its reaction, and whether its
// report layout must have a known check (checkKnown) before a decoy is made from one of its reports; Stockfish's has
// none (the WRITERS row sends its reports back with their heads moved and they are read).
struct Decoy {
    int team = 0;
    Lure lure = Lure::Toward;
    bool checked = true;
};

inline constexpr std::array DECOYS = std::to_array<Decoy>({
    {.team = 314, .lure = Lure::Toward},                  // 3.14159265: +0.136 toward, z 7.7, n 888
    {.team = 206, .lure = Lure::Away, .checked = false},  // Stockfish: -0.029 toward on the side, z -4.3, n 5327
    {.team = 264, .lure = Lure::Ahead},                   // forgot to mention: +0.255 straight on, z 10.4, n 559
});

// The DECOYS row for `team`, else an unmeasured team's.
inline Decoy decoyOf(int team) {
    for (Decoy const& d : DECOYS)
        if (d.team == team)
            return d;
    return {.team = team, .lure = Lure::Either};
}

// Free steps from `from` heading `dir` before kelp, an unknown portal or any dragon's segment in view, at most `limit`
// (tiles out of view count as free).
inline int freeRun(World const& w, int from, int dir, int limit) {
    int cur = from;
    for (int steps = 0; steps < limit; steps++) {
        int const next = w.step(cur, dir);
        if (next < 0 || (w.visible(next) && w.occId[next] >= 0))
            return steps;
        cur = next;
    }
    return limit;
}

// The tile `steps` plain torus steps from `from` heading `dir` (a report's position need not be reachable).
inline int stepsAway(World const& w, int from, int dir, int steps) {
    for (int i = 0; i < steps; i++)
        from = w.neighbour(from, dir);
    return from;
}

// Pearls in view within `far` steps (torus Manhattan) of `head`, counted by each side they lie on.
inline std::array<int, 4> pearlSides(World const& w, int head, int far) {
    std::array<int, 4> pearls{};
    int const hx = w.xOf(head), hy = w.yOf(head);
    for (int t = 0; t < w.N; t++) {
        if (!w.visible(t) || !w.pearlNow[t])
            continue;
        int dx = w.xOf(t) - hx, dy = w.yOf(t) - hy;
        dx -= dx > w.W / 2 ? w.W : dx < -w.W / 2 ? -w.W : 0;
        dy -= dy > w.H / 2 ? w.H : dy < -w.H / 2 ? -w.H : 0;
        if (std::abs(dx) + std::abs(dy) > far)
            continue;
        for (int side = 0; side < 4; side++)
            if (DX[side] * dx + DY[side] * dy > 0)
                pearls[static_cast<std::size_t>(side)]++;
    }
    return pearls;
}

// The tile a decoy for a dragon with its head at `head`, facing `facing`, should report, by the team's lure; -1 when
// the board gives no lever (no obstacle in reach, no pearl side).
inline int decoyTile(World const& w, Lure lure, int head, int facing) {
    int const near = P.spoofTrickNear, far = P.spoofTrickFar;
    int const left = (facing + 3) % 4, right = (facing + 1) % 4;
    switch (lure) {
    case Lure::Toward: {
        int const l = freeRun(w, head, left, far), r = freeRun(w, head, right, far);
        int const run = std::min(l, r);
        if (run >= far)
            return -1;
        return stepsAway(w, head, l <= r ? left : right, std::clamp(run + 1, near, far));
    }
    case Lure::Away: {
        auto const pearls = pearlSides(w, head, far);
        int const l = pearls[static_cast<std::size_t>(left)], r = pearls[static_cast<std::size_t>(right)];
        if (l == r)
            return -1;
        return stepsAway(w, head, l > r ? left : right, (near + far) / 2);
    }
    case Lure::Either: {
        auto const pearls = pearlSides(w, head, far);
        int best = -1, bestRun = far;
        for (int const side : {left, right}) {
            int const run = freeRun(w, head, side, far);
            if (run >= far)
                continue;
            if (best < 0 || pearls[static_cast<std::size_t>(side)] > pearls[static_cast<std::size_t>(best)] ||
                (pearls[static_cast<std::size_t>(side)] == pearls[static_cast<std::size_t>(best)] && run < bestRun)) {
                best = side;
                bestRun = run;
            }
        }
        if (best >= 0)
            return stepsAway(w, head, best, std::clamp(bestRun + 1, near, far));
        return decoyTile(w, Lure::Away, head, facing);
    }
    case Lure::Ahead: {
        int const run = freeRun(w, head, facing, far);
        return run >= far ? -1 : stepsAway(w, head, facing, run + 1);
    }
    }
    return -1;
}

inline bool ours(listen::Fact const& f, int W, int N, wire::Context const& ctx) {
    if (f.format < 0 || f.layout < 0)
        return false;
    listen::Format const& fmt = listen::FORMATS[static_cast<std::size_t>(f.format)];
    for (Writer const& wr : WRITERS)
        if (wr.team == fmt.team && (f.value & wr.mask) == wr.value && wr.targetX.get(f.value) == f.x &&
            wr.targetY.get(f.value) == f.y)
            return true;
    listen::Layout const& layout = fmt.layout[static_cast<std::size_t>(f.layout)];
    int const tile = f.y * W + f.x;
    return garbageTile(layout, f.value, f.heard, N, ctx) == tile || garbageTile(layout, f.value, f.heard - 1, N, ctx) == tile;
}

// The bits of `layout` that are neither its tag nor a field it decodes.
constexpr std::uint64_t undecoded(listen::Layout const& layout) {
    std::uint64_t used = layout.mask;
    for (listen::Field const f : {layout.x, layout.y, layout.tile, layout.length, layout.round, layout.id})
        if (f.bits)
            used |= ((1ULL << f.bits) - 1) << f.shift;
    return ~used;
}

// Whether `layout`'s check is known from `values`, the heard reports of that layout: nothing beyond its tag and
// fields, or its undecoded bits the same in all of them, where a check of that many bits over changing fields would
// repeat by chance at no more than the listener's false-lock odds (P.listenLockScore): bits x (distinct reports - 1)
// x ln 2. A report heard twice (a relay) is one: its check repeats with it.
inline bool checkKnown(listen::Layout const& layout, std::vector<std::uint64_t> values) {
    std::uint64_t const rest = undecoded(layout);
    if (!rest)
        return true;
    if (values.empty())
        return false;
    std::sort(values.begin(), values.end());
    values.erase(std::unique(values.begin(), values.end()), values.end());
    for (std::uint64_t const v : values)
        if ((v & rest) != (values.front() & rest))
            return false;
    return std::popcount(rest) * static_cast<double>(values.size() - 1) * std::log(2.0) >= P.listenLockScore;
}

// What a ray from our head would reach first, as far as the dragon knows: a teammate or an enemy in view, kelp (or
// our own body) in view, or ground out of view.
enum class Ray { Team, Enemy, Blocked, Unknown };

// Where a ray `dir` from `origin` goes: the first thing in view on its line. The ray into our neck leaves through our
// tail (docs/unsw/sonar.md) and is a split child's, so it counts as the team's.
inline Ray classify(World const& w, int origin, int dir, int backDir, int* hit = nullptr) {
    if (dir == backDir)
        return Ray::Team;
    int cur = origin;
    for (int steps = 0; steps < w.W + w.H; steps++) {
        int const next = w.step(cur, dir);
        if (next == KELP)
            return Ray::Blocked;
        if (next == UNKNOWN_PORTAL || !w.visible(next))
            return Ray::Unknown;
        if (w.occId[next] >= 0) {
            if (hit)
                *hit = next;
            return w.occId[next] == w.myId ? Ray::Blocked : w.occEnemy[next] ? Ray::Enemy : Ray::Team;
        }
        cur = next;
    }
    return Ray::Blocked;
}

// Every dragon's spoofer. hear() once a turn with the turn's raw sonar values; main sets `format` to the team's
// detection (listen.hpp's Listener::known); plan() after gossip planned, on the rays it left; emit() sends the fakes as
// they are (never sealed).
struct Spoofer {
    int format = -1;                  // the listener row of the enemy team the team knows of, -1 none
    std::vector<int> varied;          // garbage layouts (row x layout slots + layout) seen to carry a check (garbage())
    std::vector<std::uint64_t> heard; // the enemy's values we heard, newest last, at most P.spoofKeep
    std::array<bool, 4> sending{};
    std::array<std::uint64_t, 4> out{};
    int sent = 0; // fakes sent this turn (the debug line)
    wire::Context ctx; // our key, for garbageTile

    void hear(std::vector<std::uint64_t> const& values, int round, wire::Context const& context) {
        ctx = context;
        for (std::uint64_t const v : values) {
            if (wire::open(v, round, context))
                continue;
            if (static_cast<int>(heard.size()) >= P.spoofKeep)
                heard.erase(heard.begin());
            heard.push_back(v);
        }
    }

    // The enemy's team number, -1 before the team knows it.
    int team() const { return format < 0 ? -1 : listen::FORMATS[static_cast<std::size_t>(format)].team; }

    // A writer's fakes, newest source first: of the values heard in its format, the reports with their head moved onto
    // their target (those with no target of their own left out). None before this dragon heard one: a team changes its
    // format between versions (Stockfish's reports of 29-30 Sep are gone from its games since 30 Sep 17:00 UTC), and
    // only a report heard in this game shows the version its consumer reads.
    std::vector<std::uint64_t> written(World const& w, Writer const& f) const {
        std::vector<std::uint64_t> list;
        for (auto it = heard.rbegin(); it != heard.rend(); ++it) {
            std::uint64_t v = *it;
            if ((v & f.mask) != f.value)
                continue;
            int const tx = f.targetX.get(v), ty = f.targetY.get(v);
            if ((tx == f.headX.get(v) && ty == f.headY.get(v)) || tx >= w.W || ty >= w.H)
                continue;
            v = f.headY.put(f.headX.put(v, tx), ty);
            if (std::find(list.begin(), list.end(), v) == list.end())
                list.push_back(v);
        }
        return list;
    }

    // The heard reports that layout `k` of format row `i` reads, oldest first, when its check is known (checkKnown);
    // none otherwise. A layout whose undecoded bits were once seen to differ carries a check for the rest of the game
    // (`varied`): our teammates' fakes, heard as the enemy's, repeat the bits they copied and could otherwise crowd
    // the reports that showed the difference out of `heard`.
    std::vector<std::uint64_t> checked(World const& w, int i, int k) {
        listen::Format const& fmt = listen::FORMATS[static_cast<std::size_t>(i)];
        int const key = i * static_cast<int>(fmt.layout.size()) + k;
        if (std::find(varied.begin(), varied.end(), key) != varied.end())
            return {};
        listen::Layout const& layout = fmt.layout[static_cast<std::size_t>(k)];
        std::uint64_t const rest = undecoded(layout);
        std::vector<std::uint64_t> mine;
        for (std::uint64_t const v : heard)
            if (listen::decode(layout, v, w.round, w.W, w.H))
                mine.push_back(v);
        if (std::any_of(mine.begin(), mine.end(), [&](std::uint64_t v) { return (v & rest) != (mine[0] & rest); })) {
            varied.push_back(key);
            return {};
        }
        return checkKnown(layout, mine) ? mine : std::vector<std::uint64_t>{};
    }

    // Garbage for the detected team (P.spoofGarbage): for each layout of its verified report rows (any version, not
    // only the row that detected it), of its heard reports, newest first, when that layout's check is known
    // (checkKnown), the report with its position moved to garbageTile (keyed on the round and its other bits), its other
    // fields and undecoded bits as heard.
    std::vector<std::uint64_t> garbage(World const& w) {
        std::vector<std::uint64_t> list;
        if (!P.spoofGarbage)
            return list;
        int const enemy = team();
        for (int i = 0; i < static_cast<int>(listen::FORMATS.size()); i++) {
            listen::Format const& fmt = listen::FORMATS[static_cast<std::size_t>(i)];
            if (fmt.team != enemy || fmt.kind != listen::Kind::Report || !fmt.verified)
                continue;
            for (int k = 0; k < fmt.layouts; k++) {
                listen::Layout const& layout = fmt.layout[static_cast<std::size_t>(k)];
                std::uint64_t const rest = undecoded(layout);
                auto const mine = checked(w, i, k);
                for (auto it = mine.rbegin(); it != mine.rend(); ++it) {
                    auto fact = *listen::decode(layout, *it, w.round, w.W, w.H);
                    int const tile = garbageTile(layout, listen::encode(layout, fact, w.W) | (*it & rest), w.round, w.N, ctx);
                    fact.x = w.xOf(tile);
                    fact.y = w.yOf(tile);
                    std::uint64_t const fake = listen::encode(layout, fact, w.W) | (*it & rest);
                    if (std::find(list.begin(), list.end(), fake) == list.end())
                        list.push_back(fake);
                }
            }
        }
        return list;
    }

    // This turn's fakes for the team the team knows of: its writer's, else garbage, else none.
    std::vector<std::uint64_t> fakes(World const& w) {
        if (writerOf(team()) >= 0) {
            std::vector<std::uint64_t> list;
            for (Writer const& f : WRITERS)
                if (f.team == team())
                    for (std::uint64_t const v : written(w, f))
                        list.push_back(v);
            return list;
        }
        return format >= 0 ? garbage(w) : std::vector<std::uint64_t>{};
    }

    // A decoy for the enemy dragon whose segment is at `hit` (P.spoofTrick): when that dragon's head is in view and its
    // surroundings give its team's lure a lever (decoyOf, decoyTile), its newest heard report of the team's verified
    // own-head report layouts (any version; a checked team's only where the check is known) with its position moved
    // to the decoy tile and its round, if it has one, made this one; its other bits (length, id) as heard.
    std::optional<std::uint64_t> decoy(World const& w, int hit) {
        if (!P.spoofTrick || hit < 0)
            return std::nullopt;
        Decoy const d = decoyOf(team());
        int head = -1;
        for (SeenDragon const& s : w.dragons)
            if (s.id == w.occId[hit] && s.head >= 0)
                head = s.head;
        if (head < 0)
            return std::nullopt;
        int const tile = decoyTile(w, d.lure, head, w.facing[head]);
        if (tile < 0)
            return std::nullopt;
        std::vector<std::pair<listen::Layout const*, std::vector<std::uint64_t>>> sources;
        for (int i = 0; i < static_cast<int>(listen::FORMATS.size()); i++) {
            listen::Format const& fmt = listen::FORMATS[static_cast<std::size_t>(i)];
            if (fmt.team == d.team && fmt.kind == listen::Kind::Report && fmt.verified)
                for (int k = 0; k < fmt.layouts; k++)
                    if (!fmt.layout[static_cast<std::size_t>(k)].sighting)
                        sources.emplace_back(&fmt.layout[static_cast<std::size_t>(k)], d.checked ? checked(w, i, k) : heard);
        }
        for (auto it = heard.rbegin(); it != heard.rend(); ++it)
            for (auto const& [layout, mine] : sources) {
                auto fact = listen::decode(*layout, *it, w.round, w.W, w.H);
                if (!fact || std::find(mine.begin(), mine.end(), *it) == mine.end())
                    continue;
                fact->x = w.xOf(tile);
                fact->y = w.yOf(tile);
                fact->round = w.round;
                std::uint64_t const value = listen::encode(*layout, *fact, w.W) | (*it & undecoded(*layout));
                if (auto const back = listen::decode(*layout, value, w.round, w.W, w.H);
                    back && back->x == fact->x && back->y == fact->y)
                    return value;
            }
        // A version the table's report rows don't read yet (Stockfish's 0xC): a WRITERS row's heard report with its
        // head moved to the decoy tile, its other bits as heard.
        for (auto it = heard.rbegin(); it != heard.rend(); ++it)
            for (Writer const& f : WRITERS)
                if (f.team == d.team && (*it & f.mask) == f.value && w.xOf(tile) < 128 && w.yOf(tile) < 128)
                    return f.headY.put(f.headX.put(*it, w.xOf(tile)), w.yOf(tile));
        return std::nullopt;
    }

    // Picks this turn's fakes after gossip planned its rays: a fake goes on every ray gossip and the doorway probes
    // left free unless it reaches a teammate in view, kelp or our own body first (rays reaching an enemy in view are
    // always free: gossip's rayUseful). A ray reaching an enemy dragon carries its decoy when it has one, else the
    // team's fake.
    void plan(World const& w, int origin, int backDir, std::array<bool, 4> const& gossipSending) {
        sending.fill(false);
        sent = 0;
        if (!P.spoof || origin < 0 || format < 0)
            return;
        auto const list = fakes(w);
        if (list.empty() && !P.spoofTrick)
            return;
        std::size_t next = static_cast<std::size_t>(w.round + w.myId);
        for (int dir = 0; dir < 4; dir++) {
            if (gossipSending[dir])
                continue;
            int hit = -1;
            Ray const ray = classify(w, origin, dir, backDir, &hit);
            if (ray == Ray::Team || ray == Ray::Blocked)
                continue;
            auto const lure = ray == Ray::Enemy ? decoy(w, hit) : std::nullopt;
            if (!lure && list.empty())
                continue;
            sending[dir] = true;
            out[dir] = lure ? *lure : list[next++ % list.size()];
            sent++;
        }
    }

    void emit(unswbc::Controller& ct) const {
        for (int dir = 0; dir < 4; dir++)
            if (sending[dir])
                ct.send_sonar(unswbc::Direction(DIR_CHAR[dir]), out[dir]);
    }
};

} // namespace bot::spoof
