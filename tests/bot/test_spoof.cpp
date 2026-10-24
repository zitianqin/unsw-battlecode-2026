// Spoofing (spoof.hpp): once the team knows the enemy's team, fakes that pass its check go on the rays our gossip
// leaves, never on one that reaches a teammate in view; a split child inherits the team at once; garbage goes only
// where the team's check is known; neither our own code under another key nor noise names a team.
#include "support.hpp"
#include "belief.hpp"
#include "gossip.hpp"
#include "listen.hpp"
#include "spoof.hpp"

#include <optional>
#include <string>

using test::require;
namespace S = bot::spoof;
namespace L = bot::listen;

namespace {

// A Stockfish report: head (hx, hy), target (tx, ty), and low bits of its own.
std::uint64_t stockfish(int hx, int hy, int tx, int ty, std::uint64_t low) {
    return 3ULL << 60 | static_cast<std::uint64_t>(hx) << 53 | static_cast<std::uint64_t>(hy) << 46 |
           static_cast<std::uint64_t>(tx) << 39 | static_cast<std::uint64_t>(ty) << 32 | (low & 0xFFFFFFFE);
}

// The team knows it plays Stockfish (206): the listener's detection names one of its rows (test_listen.cpp's tag case
// shows the lock).
L::Detection stockfishDetected() {
    for (int i = 0; i < static_cast<int>(L::FORMATS.size()); i++)
        if (L::FORMATS[static_cast<std::size_t>(i)].team == 206)
            return {.format = i, .confidence = 40};
    return {};
}

// A Stockfish dragon's spoofer that heard eight of their reports.
S::Spoofer heardStockfish(bot::World const& w, bot::wire::Context const& ctx) {
    S::Spoofer s;
    std::vector<std::uint64_t> heard;
    for (int i = 0; i < 8; i++)
        heard.push_back(stockfish(2 + i, 3, 12, 1 + i, 0x1234u * (i + 1)));
    s.hear(heard, w.round, ctx);
    s.format = stockfishDetected().format;
    return s;
}

void stockfish_gets_its_own_targets_back_as_phantoms() {
    auto w = test::world();
    int const head = w.head; // (8, 8)
    test::dragon(w, 20, true, w.tileOf(11, 8), 3); // an enemy head east
    test::dragon(w, 7, false, w.tileOf(8, 5), 3);  // a teammate north
    test::wall(w, head, 3);                        // kelp west
    for (int y = 10; y < 16; y++)                  // south is out of view
        w.visStamp[w.tileOf(8, y)] = 0;
    bot::Belief b;
    b.init(w);
    auto s = heardStockfish(w, b.context());

    s.plan(w, head, -1, std::array<bool, 4>{false, false, true, false}); // gossip took south
    require(!s.sending[0], "the ray to a teammate in view carries no fake");
    require(s.sending[1], "the ray to an enemy head carries a fake");
    require(!s.sending[2], "a ray gossip took keeps its gossip");
    require(!s.sending[3], "the kelp ray carries nothing");
    auto const& f = S::WRITERS[0];
    std::uint64_t const v = s.out[1];
    require(v >> 60 == 3 && !(v & 1) && f.headX.get(v) == 12 && f.headX.get(v) == f.targetX.get(v) &&
                f.headY.get(v) == f.targetY.get(v),
            "the fake is one of their reports with its head moved onto its target");
    s.plan(w, head, -1, std::array<bool, 4>{});
    require(s.sending[2], "an unseen ray gossip leaves carries a fake");
}

// The parent knows the team and splits: the listen record goes on a ray that reaches the child, so the child knows
// the team on its first turn; it fakes once it hears a report in the writer's format (the version its consumer reads).
void a_split_child_inherits_the_team() {
    auto w = test::world();
    test::body(w, {w.tileOf(8, 8), w.tileOf(7, 8), w.tileOf(6, 8), w.tileOf(5, 8)});
    w.facing[w.head] = w.facing[w.body[1]] = 1;
    bot::Belief b;
    b.init(w);
    auto const ctx = b.context();
    bot::Gossip g;
    g.detection = stockfishDetected();
    g.detectionSent = g.detection; // sent a round ago: not due for teammates
    g.detectionSentRound = w.round - 1;
    g.plan(w, b, w.head, 3, true, 2);
    int const ray = bot::KelpKnowledge::childRay(w, 2);
    require(ray >= 0 && g.sending[ray] && L::decodeDetection(g.out[ray], ctx) == g.detection,
            "the ray to the child carries the team, though the record was not due");
    test::dragon(w, 7, false, w.tileOf(8, 4), 3); // a teammate north
    g.detectionSent = {};                          // the team is new this turn
    g.plan(w, b, w.head, 3, true, 2);
    require(g.sending[ray] && g.sending[0] && L::decodeDetection(g.out[0], ctx) == g.detection,
            "a new team goes to the child and to the other teammates alike");

    auto child = test::world(); // its head on the parent's old tail; an enemy head east of it
    child.myId = 2;
    test::dragon(child, 20, true, child.tileOf(11, 8), 3);
    L::Listener listener;
    listener.hear(child, ctx, {bot::wire::seal(g.out[ray], w.round, ctx)});
    S::Spoofer s;
    s.format = listener.known().format;
    require(s.team() == 206, "the child knows the team from its first message");
    s.plan(child, child.head, 3, std::array<bool, 4>{});
    require(s.sending == std::array<bool, 4>{}, "with no report of theirs heard, it fakes nothing");
    s.hear({stockfish(2, 3, 8, 6, 0)}, child.round, ctx);
    s.plan(child, child.head, 3, std::array<bool, 4>{});
    auto const& f = S::WRITERS[0];
    require(s.sending[1] && f.headX.get(s.out[1]) == 8 && f.headY.get(s.out[1]) == 6,
            "after one, the ray to the enemy carries it with its head on its target");
}

// A verified report format with bits we don't decode gets garbage only once those bits were the same in enough of
// the reports heard (checkKnown); the garbage decodes as theirs, at another tile, its undecoded bits as heard.
void garbage_only_where_the_check_is_known() {
    int format = -1;
    for (int i = 0; i < static_cast<int>(L::FORMATS.size()) && format < 0; i++) {
        L::Format const& fmt = L::FORMATS[static_cast<std::size_t>(i)];
        auto const reports = std::count_if(L::FORMATS.begin(), L::FORMATS.end(), [&](L::Format const& other) {
            return other.team == fmt.team && other.kind == L::Kind::Report && other.verified;
        });
        if (fmt.kind == L::Kind::Report && fmt.verified && fmt.layouts == 1 && fmt.layout[0].x.bits &&
            std::popcount(S::undecoded(fmt.layout[0])) >= 8 && S::writerOf(fmt.team) < 0 && reports == 1)
            format = i;
    }
    require(format >= 0, "the table has a team whose one verified report has undecoded bits");
    L::Layout const& layout = L::FORMATS[static_cast<std::size_t>(format)].layout[0];
    std::uint64_t const rest = S::undecoded(layout);
    int const bits = std::popcount(rest);
    auto w = test::world();
    int const needed = static_cast<int>(std::ceil(P.listenLockScore / (bits * std::log(2.0)))) + 1;
    auto report = [&](int n, std::uint64_t undecodedBits) {
        L::Fact f{.round = w.round, .x = 1 + n % 10, .y = 2, .length = 3, .id = 1};
        return L::encode(layout, f, w.W) | (undecodedBits & rest);
    };
    S::Spoofer s;
    s.format = format;
    for (int n = 0; n < needed - 1; n++)
        s.heard.push_back(report(n, 0x5A5A5A5A5A5A5A5AULL));
    for (int n = 0; n < needed; n++)
        s.heard.push_back(report(0, 0x5A5A5A5A5A5A5A5AULL)); // relays of one report
    require(s.fakes(w).empty(), "too few distinct reports to rule out a check over the fields: no garbage");
    s.heard.push_back(report(needed - 1, 0x5A5A5A5A5A5A5A5AULL));
    auto const list = s.fakes(w);
    require(!list.empty(), "enough reports with the same undecoded bits: garbage");
    bool keyed = true;
    for (std::uint64_t const v : list) {
        auto const fact = L::decode(layout, v, w.round, w.W, w.H);
        require(fact && (v & rest) == (0x5A5A5A5A5A5A5A5AULL & rest), "the garbage passes their format and keeps its bits");
        keyed = keyed && w.tileOf(fact->x, fact->y) == S::garbageTile(layout, v, w.round, w.N, s.ctx);
    }
    // The tile is a hash under our build's key (a hash of the source), so which tile it is changes with any edit.
    require(keyed, "at the keyed tile its other bits and the round pick");
    bot::wire::Context const ctx{false, w.W, w.H};
    s.ctx = ctx;
    int teammates = 0, real = 0;
    for (std::uint64_t const v : s.fakes(w)) {
        auto const heardNext = L::decode(format, v, w.round + 1, w.W, w.H); // a lower id hears it next round
        teammates += heardNext && S::ours(*heardNext, w.W, w.N, ctx);
    }
    for (std::uint64_t const v : s.heard)
        real += S::ours(*L::decode(format, v, w.round, w.W, w.H), w.W, w.N, ctx);
    require(teammates == static_cast<int>(s.fakes(w).size()) && real == 0,
            "a teammate knows our garbage by its keyed tile, and the enemy's own reports are not taken for ours");
    s.heard.push_back(report(0, 0xA5A5A5A5A5A5A5A5ULL));
    require(s.fakes(w).empty(), "undecoded bits that change (a check) stop it");
    s.heard.assign(needed + 1, 0);
    for (int n = 0; n <= needed; n++)
        s.heard[static_cast<std::size_t>(n)] = report(n, 0x5A5A5A5A5A5A5A5AULL);
    require(s.fakes(w).empty(), "for the rest of the game, though later reports (our own garbage relayed) agree");
}

// The first own-head layout of `team`'s verified report rows (the one its heard reports are written in here), or null.
L::Layout const* reportLayout(int team) {
    for (L::Format const& fmt : L::FORMATS)
        if (fmt.team == team && fmt.kind == L::Kind::Report && fmt.verified)
            for (int k = 0; k < fmt.layouts; k++)
                if (!fmt.layout[static_cast<std::size_t>(k)].sighting)
                    return &fmt.layout[static_cast<std::size_t>(k)];
    return nullptr;
}

// A row naming `team` that takes part in detection, or -1.
int teamRow(int team) {
    for (int i = 0; i < static_cast<int>(L::FORMATS.size()); i++)
        if (L::FORMATS[static_cast<std::size_t>(i)].team == team && L::enabled(i))
            return i;
    return -1;
}

// Forty distinct reports of `team` in reportLayout, their undecoded bits the same (a known check, one bit of it too).
std::vector<std::uint64_t> reportsOf(int team, bot::World const& w) {
    L::Layout const& layout = *reportLayout(team);
    std::vector<std::uint64_t> list;
    for (int n = 0; n < 40; n++)
        list.push_back(L::encode(layout, {.round = w.round, .x = 1 + n % 12, .y = 2 + n / 12, .length = 5, .id = 3}, w.W) |
                       (0x5A5A5A5A5A5A5A5AULL & S::undecoded(layout)));
    return list;
}

// What the ray east from our head (8, 8; our body runs west) to an enemy head at (11, 8) facing `facing` carries, having
// heard `reports` of `team`, with pearls at `pearls`; decoded in the team's reportLayout.
struct Sent {
    std::uint64_t value = 0;
    std::optional<L::Fact> fact;
};
Sent decoyEast(int team, int facing, std::vector<std::uint64_t> const& reports, std::vector<int> const& pearls = {}) {
    auto w = test::world();
    test::body(w, {w.tileOf(8, 8), w.tileOf(7, 8), w.tileOf(6, 8), w.tileOf(5, 8)});
    test::dragon(w, 20, true, w.tileOf(11, 8), 3);
    w.facing[w.tileOf(11, 8)] = facing;
    for (int const t : pearls)
        test::pearl(w, t);
    S::Spoofer s;
    s.heard = reports;
    s.format = teamRow(team);
    s.plan(w, w.head, 3, std::array<bool, 4>{});
    if (!s.sending[1])
        return {};
    return {s.out[1], L::decode(*reportLayout(team), s.out[1], w.round, w.W, w.H)};
}

// P.spoofTrick: every team we can detect with a verified own-head report layout gets, on the ray to its dragon, a decoy
// that passes that layout (tag and fields; undecoded bits and length as heard) and decodes to the tile its lure picks:
// here, facing north with our body 3 tiles to its left, a teammate 4 tiles left, on our body.
void every_detectable_team_gets_a_decoy_it_reads() {
    auto const w = test::world();
    std::vector<int> teams;
    for (L::Format const& fmt : L::FORMATS)
        if (fmt.team < L::FAMILY_BASE && teamRow(fmt.team) >= 0 && reportLayout(fmt.team) &&
            std::find(teams.begin(), teams.end(), fmt.team) == teams.end())
            teams.push_back(fmt.team);
    require(teams.size() >= 40, "the table has the detectable teams with a verified report (" +
                                    std::to_string(teams.size()) + ")");
    std::string wrong;
    for (int const team : teams) {
        if (team == 264 || team == 206) // lures of their own (decoys_steer_the_measured_teams)
            continue;
        L::Layout const& layout = *reportLayout(team);
        auto const sent = decoyEast(team, 0, reportsOf(team, w));
        bool const ok = sent.fact && sent.fact->x == 7 && sent.fact->y == 8 &&
                        (sent.value & S::undecoded(layout)) == (0x5A5A5A5A5A5A5A5AULL & S::undecoded(layout)) &&
                        (!layout.length.bits || sent.fact->length == 5);
        if (!ok)
            wrong += " " + std::to_string(team);
    }
    require(wrong.empty(), "decoys that don't read as intended:" + wrong);
}

// The measured teams' placements: 314 turns toward a teammate beside it (onto our body, this round), 264 holds course
// after one ahead (onto our head), Stockfish turns away from one on its pearl side (its target bits as heard). With no
// lever on the board, today's fake goes instead.
void decoys_steer_the_measured_teams() {
    auto const w = test::world();
    auto const pi = decoyEast(314, 0, reportsOf(314, w));
    require(pi.fact && pi.fact->x == 7 && pi.fact->y == 8 && pi.fact->round == w.round && pi.fact->id == 3,
            "314: a teammate 4 tiles to its left, on our body, this round");
    auto const forgot = decoyEast(264, 3, reportsOf(264, w)); // facing west, at us
    require(forgot.fact && forgot.fact->x == 8 && forgot.fact->y == 8 && forgot.fact->length == 5,
            "264: a teammate ahead, on our head");
    auto const open = decoyEast(264, 0, reportsOf(264, w)); // facing north, nothing ahead in reach
    require(!open.fact || open.fact->x != 11, "264 with an open road: no decoy ahead");

    std::vector<std::uint64_t> sf;
    for (int i = 0; i < 8; i++)
        sf.push_back(stockfish(2 + i, 3, 12, 1 + i, 0x1230u * (i + 1) & ~5ULL));
    auto const fish = decoyEast(206, 0, sf, {w.tileOf(13, 8)}); // a pearl to its right (east)
    require(fish.fact && fish.fact->x == (11 + (P.spoofTrickNear + P.spoofTrickFar) / 2) % 16 && fish.fact->y == 8 &&
                S::WRITERS[0].targetX.get(fish.value) == 12,
            "206: a teammate on its pearl side, its target as heard");
    auto const plain = decoyEast(206, 0, sf); // no pearl in view: the writer's fake
    require(plain.value && S::WRITERS[0].headX.get(plain.value) == S::WRITERS[0].targetX.get(plain.value) &&
                S::WRITERS[0].headY.get(plain.value) == S::WRITERS[0].targetY.get(plain.value),
            "206 with no pearl side: today's fake, its head on its target");

    // Stockfish's 0xC version (ranked since 1 Oct): the same decoy and fake, in the version heard.
    for (auto& v : sf)
        v = (v & ~(0xFULL << 60)) | 0xCULL << 60;
    auto const& c = S::WRITERS[1];
    auto const fishC = decoyEast(206, 0, sf, {w.tileOf(13, 8)});
    require(fishC.value >> 60 == 0xC &&
                c.headX.get(fishC.value) == (11 + (P.spoofTrickNear + P.spoofTrickFar) / 2) % 16 &&
                c.headY.get(fishC.value) == 8 && c.targetX.get(fishC.value) == 12,
            "206 0xC: a teammate on its pearl side, in their new version");
    auto const plainC = decoyEast(206, 0, sf);
    require(plainC.value >> 60 == 0xC && c.headX.get(plainC.value) == c.targetX.get(plainC.value) &&
                c.headY.get(plainC.value) == c.targetY.get(plainC.value),
            "206 0xC with no pearl side: the writer's fake in their new version");
}

void no_team_in_our_own_codes_values_or_noise() {
    auto w = test::world();
    bot::Belief b;
    b.init(w);
    auto const ctx = b.context();
    auto other = ctx;
    other.teamB = !ctx.teamB;
    S::Spoofer s;
    L::Listener listener;
    std::uint64_t z = 0x9E3779B97F4A7C15ULL;
    for (int i = 0; i < 20000; i++) {
        z = bot::wire::mix(z + 0x9E3779B97F4A7C15ULL);
        // random-looking values (a cipher's, or our code under another build's key) and our code on the other side
        std::vector<std::uint64_t> const values{z, bot::wire::seal(z & bot::wire::BODY_MASK, w.round, other)};
        s.hear(values, w.round, ctx);
        listener.hear(w, ctx, values);
    }
    require(listener.team() < 0 && listener.known().format < 0, "no team is named");
    s.format = listener.known().format;
    s.plan(w, w.head, -1, std::array<bool, 4>{});
    require(s.sending == std::array<bool, 4>{}, "so nothing is faked");
}

} // namespace

int main() {
    return test::suite({
        {"stockfish gets its own targets back as phantoms", stockfish_gets_its_own_targets_back_as_phantoms},
        {"a split child inherits the team", a_split_child_inherits_the_team},
        {"garbage only where the check is known", garbage_only_where_the_check_is_known},
        {"no team in our own code's values or noise", no_team_in_our_own_codes_values_or_noise},
        {"every detectable team gets a decoy it reads", every_detectable_team_gets_a_decoy_it_reads},
        {"decoys steer the measured teams", decoys_steer_the_measured_teams},
    });
}
