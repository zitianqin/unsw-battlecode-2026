// The passive sonar listener (listen.hpp): real enemy values from replays decode to the true head, no single value
// locks onto another team, the detector locks onto the sender's team after enough values, a tag names a team we
// can't decode while zeros name nobody, every spec entry
// round-trips, a field-XORing team decodes as its report says, and a detection travels to a teammate over gossip.
#include "support.hpp"
#include "gossip.hpp"
#include "listen.hpp"

#include <algorithm>
#include <cstdint>
#include <string>
#include <vector>

using test::require;
namespace L = bot::listen;

namespace {

// A value some team's dragon sent in a ranked game, with the true state (from the replay).
struct Heard {
    int team;
    std::uint64_t value;
    int W, H, round, x, y, length;
};

// SSS (91) match 725191: dragon 72, length 2, at (35, 9) in round 121. forgot to mention (264) match 720136: dragon 4
// at (14, 4), length 2, round 20. okbro (22) match 688158: dragon 0 at (0, 1), length 4, round 0.
constexpr Heard HEARD[] = {
    {91, 0xf5e012014649e68dULL, 48, 24, 121, 35, 9, 2},
    {264, 0x6b5c0101fc04813aULL, 60, 40, 20, 14, 4, 2},
    {22, 0x1000100100003044ULL, 32, 32, 0, 0, 1, 4},
};

// A real value decodes to the true head under its team's verified format, and though another team's weak
// recogniser (no tag, only its fields' ranges) may take it too, no single value is evidence enough to lock onto
// another team.
void a_real_value_decodes_to_the_true_head_and_locks_no_other_team() {
    for (Heard const& h : HEARD) {
        bool found = false;
        for (int i = 0; i < static_cast<int>(L::FORMATS.size()); i++) {
            auto const f = L::trusted(i) ? L::decode(i, h.value, h.round, h.W, h.H) : std::nullopt;
            if (f && L::FORMATS[static_cast<std::size_t>(i)].team == h.team) {
                require(f->x == h.x && f->y == h.y, "team " + std::to_string(h.team) + " decodes to the true head");
                require(f->length < 0 || f->length == h.length, "and its true length");
                found = true;
            }
        }
        require(found, "a verified format of team " + std::to_string(h.team) + " reads its value");
        for (int i = 0; i < static_cast<int>(L::FORMATS.size()); i++)
            if (L::enabled(i) && L::FORMATS[static_cast<std::size_t>(i)].team != h.team &&
                L::decode(i, h.value, h.round, h.W, h.H))
                require(L::evidence(i, true) < P.listenLockScore,
                        std::string(L::FORMATS[static_cast<std::size_t>(i)].name) + " would lock on one value of team " +
                            std::to_string(h.team));
    }
}

void the_detector_locks_onto_the_sender_after_enough_values() {
    Heard const& h = HEARD[0];
    L::Listener listener;
    for (int n = 1; n < P.listenLockHits; n++)
        listener.offer(h.value, h.round, h.W, h.H);
    require(listener.team() < 0, "no lock before P.listenLockHits values");
    for (int n = 0; n < 8 && listener.team() < 0; n++)
        listener.offer(h.value, h.round, h.W, h.H);
    require(listener.team() == h.team, "the detector locks onto SSS");
    require(!listener.facts.empty() && listener.facts.back().x == h.x && listener.facts.back().y == h.y,
            "and stores the head it heard (once: the same fact is not kept twice)");
    require(listener.facts.size() == 1, "a repeated value is one fact");
    for (Heard const& other : HEARD)
        if (other.team != h.team)
            for (int n = 0; n < 3; n++)
                listener.offer(other.value, other.round, other.W, other.H);
    require(listener.team() == h.team, "a few values of other teams do not move the lock");
}

// A team is named by its tag bits alone, from values no report layout reads: Stockfish's (206) first 8 non-zero values heard in held-out
// match 657009 lock onto it through a top-20 tag recogniser (which reads no fields, so the board size doesn't matter).
// Zeros, which PPP (27) and others send as filler, lock onto nobody: no recogniser takes 0.
void a_tag_recogniser_names_a_team_we_cannot_decode() {
    constexpr std::uint64_t STOCKFISH[] = {0xc263c98f00000000ULL, 0xc202c80b06005010ULL, 0xc202c80b04005110ULL,
                                           0xc1c3440d04015110ULL, 0xc20288041a4429a0ULL, 0xc202480504005310ULL,
                                           0xc246491600000000ULL, 0xc24609130400cc10ULL};
    L::Listener listener;
    int round = 7;
    for (int pass = 0; pass < 2 && listener.team() < 0; pass++)
        for (std::uint64_t const v : STOCKFISH)
            listener.offer(v, round++, 40, 40);
    require(listener.team() == 206, "Stockfish's values lock onto Stockfish");
    L::Listener zeros;
    for (int n = 0; n < 40; n++)
        zeros.offer(0, n, 40, 40);
    require(zeros.team() < 0, "a stream of zeros locks onto nobody");
}

// A family row (a tag several teams share, for spoofing) names its members, stands for each of them, and never takes
// part in detection: Team SKKU's 0xA top nibble is shared by 3.14159265 (314) among others.
void a_family_row_names_its_members_and_never_locks() {
    int found = -1;
    for (int i = 0; i < static_cast<int>(L::FORMATS.size()); i++) {
        L::Format const& fmt = L::FORMATS[static_cast<std::size_t>(i)];
        if (fmt.family > 0) {
            require(fmt.team >= L::FAMILY_BASE, std::string(fmt.name) + " has a family id, not a team's");
            require(!L::enabled(i), std::string(fmt.name) + " takes no part in detection");
            if (fmt.layout[0].value == 0xa000000000000000ULL)
                found = i;
        }
    }
    require(found >= 0, "the 0xA family is in the table");
    auto const members = L::members(L::FORMATS[static_cast<std::size_t>(found)].team);
    require(std::ranges::find(members, 249) != members.end() && std::ranges::find(members, 314) != members.end(),
            "its members include Team SKKU and 3.14159265");
    require(L::covers(found, 249) && !L::covers(found, 157), "it stands for its members, never for us");
    require(L::members(91) == std::vector<int>{91}, "a team's own id stands for itself");
}

// Every team has an attack class (tools/sonar/attacks.py) whose payload its own recogniser takes: a forged SSS (91)
// report decodes as the lie we put in, a garbage value passes its check, and we are never a target.
void every_team_has_an_attack_its_recogniser_takes() {
    for (auto const& [team, c] : L::ATTACK_TEAMS) {
        require(team != 157, "we are never attacked");
        require(c >= 0 && c < static_cast<int>(L::ATTACKS.size()), "team " + std::to_string(team) + "'s class exists");
        L::Attack const& a = L::ATTACKS[static_cast<std::size_t>(c)];
        if (a.kind == L::AttackKind::Garbage) {
            L::Layout const& layout = L::FORMATS[static_cast<std::size_t>(a.format)].layout[0];
            std::uint64_t const v = a.value | (0x5a5a5a5a5a5a5a5aULL & ~a.mask);
            require(a.mask != 0 && a.mask == layout.mask && (v & layout.mask) == layout.value,
                    "a garbage value passes team " + std::to_string(team) + "'s check");
        }
    }
    auto const sss = std::ranges::find_if(L::ATTACK_TEAMS, [](auto const& p) { return p[0] == 91; });
    require(sss != L::ATTACK_TEAMS.end(), "SSS has a class");
    L::Attack const& a = L::ATTACKS[static_cast<std::size_t>((*sss)[1])];
    require(a.kind == L::AttackKind::Forge, "SSS is forged");
    L::Fact const lie{.round = 98, .x = 7, .y = 9, .length = 5, .id = 3};
    auto const f = L::decode(a.format, L::encode(L::FORMATS[static_cast<std::size_t>(a.format)].layout[0], lie, 40), 100,
                             40, 30);
    require(f && f->x == 7 && f->y == 9, "the forged value reads as the lie");
}

// A layout whose heads matched replays under half the time (Layout::untrusted, tools/sonar/decoybits.py) is never
// believed: a value it reads, from a team we follow, stores no fact, while its team's sound layouts still do.
void an_untrusted_layout_stores_no_fact() {
    int checked = 0;
    for (int i = 0; i < static_cast<int>(L::FORMATS.size()); i++) {
        L::Format const& fmt = L::FORMATS[static_cast<std::size_t>(i)];
        for (int k = 0; k < fmt.layouts; k++) {
            L::Layout const& layout = fmt.layout[static_cast<std::size_t>(k)];
            if (!layout.untrusted || !L::trusted(i) || (!layout.x.bits && !layout.tile.bits))
                continue;
            L::Fact const truth{.round = 198, .x = 5, .y = 3, .length = 4, .id = 3};
            auto const f = L::decode(layout, L::encode(layout, truth, 40), 200, 40, 30);
            require(f && !f->believed, std::string(fmt.name) + "'s untrusted layout reads but is not believed");
            L::Listener listener;
            listener.follow(fmt.team);
            listener.offer(L::encode(layout, truth, 40), 200, 40, 30);
            require(std::ranges::none_of(listener.facts, [](L::Fact const& x) { return !x.believed; }),
                    std::string(fmt.name) + ": no untrusted fact is stored");
            checked++;
        }
    }
    require(checked > 0, "the table has untrusted layouts to check");
}

// Every layout of every spec entry reads back the state it encodes (tools/sonar/spec.toml round-trips).
void every_spec_entry_round_trips() {
    int const W = 40, H = 30, round = 200;
    for (int i = 0; i < static_cast<int>(L::FORMATS.size()); i++) {
        L::Format const& fmt = L::FORMATS[static_cast<std::size_t>(i)];
        for (int k = 0; k < fmt.layouts; k++) {
            L::Layout const& layout = fmt.layout[static_cast<std::size_t>(k)];
            if (!layout.x.bits && !layout.tile.bits)
                continue;
            L::Fact truth{.round = round - 2, .x = 5, .y = 3, .length = 4, .id = 3};
            auto const f = L::decode(layout, L::encode(layout, truth, W), round, W, H);
            std::string const name = std::string(fmt.name) + " " + fmt.version;
            require(f && f->x == truth.x && f->y == truth.y, name + " round-trips its head");
            require(!layout.length.bits || f->length == truth.length, name + " round-trips its length");
            require(!layout.round.bits || f->round == truth.round, name + " round-trips its round");
            require(!layout.id.bits || f->id == truth.id, name + " round-trips its id");
        }
    }
}

// A team that XORs its fields with constants (dev test 1 :P, 545) reads as the keyed-sonar report's own decoder
// (~/.bc-loop/reports/keyed-sonar-2026-10-01.md, decode545) says; a Team SKKU value (top nibble 0xA) is recognised.
void a_flipped_layout_reads_as_its_report_says() {
    int const W = 32, H = 16;
    int found = 0;
    for (int i = 0; i < static_cast<int>(L::FORMATS.size()); i++) {
        L::Format const& fmt = L::FORMATS[static_cast<std::size_t>(i)];
        if (fmt.team == 249 && std::string(fmt.version) == "keyed-report-2026-10-01") {
            found++;
            require(L::decode(i, 0xa123456789abcdefULL, 100, W, H) && !L::decode(i, 0xb123456789abcdefULL, 100, W, H),
                    "SKKU's tag is its top nibble");
        }
        if (fmt.team != 545 || std::string(fmt.version) != "keyed-report-2026-09-29")
            continue;
        found++;
        for (std::uint64_t v = 0x9e3779b97f4a7c15ULL, n = 0; n < 2000; n++, v = v * 6364136223846793005ULL + 1) {
            int const round = static_cast<int>(((v >> 47) & 0x1FF) ^ 0x83);
            int const tile = static_cast<int>(((v >> 22) & 0xFFF) ^ 0xE95);
            int const length = static_cast<int>(((v >> 12) & 0x7F) ^ 0x1E);
            auto const f = L::decode(i, v, round, W, H);
            require(f.has_value() == (tile < W * H && length >= 2), "545 reads a value when its tile and length are plausible");
            if (f)
                require(f->round == round && f->x == tile % W && f->y == tile / W && f->length == length &&
                            f->id == static_cast<int>(((v >> 34) & 0x3FF) ^ 0x230),
                        "545's fields are the report's");
        }
    }
    require(found == 2, "the table has the keyed report's 545 and 249 rows");
}

// Dragon 1 detected SSS; its gossip carries the detection, and dragon 2, which heard no enemy, then knows it too and
// passes it on.
void a_detection_reaches_a_teammate_over_gossip() {
    auto const ctx = test::wire(false);
    L::Listener one;
    for (int n = 0; n < 10; n++)
        one.offer(HEARD[0].value, HEARD[0].round, HEARD[0].W, HEARD[0].H);
    require(one.team() == 91, "dragon 1 locked onto SSS");
    auto w1 = test::world();
    test::dragon(w1, 2, false, w1.tileOf(11, 8), 2); // a teammate east of our head at (8, 8), so that ray is useful
    bot::Belief b1;
    b1.init(w1);
    bot::Gossip g1;
    g1.detection = one.known();
    g1.plan(w1, b1, w1.head, -1);
    std::vector<std::uint64_t> air;
    for (int dir = 0; dir < 4; dir++)
        if (g1.sending[dir])
            air.push_back(bot::wire::seal(g1.out[dir], w1.round, ctx));
    require(!air.empty(), "dragon 1 sends");
    auto w2 = test::world();
    w2.myId = 2;
    L::Listener two;
    two.hear(w2, ctx, air);
    require(two.known() == one.known() && two.heardValues == 0, "dragon 2 knows dragon 1's detection, not as an enemy value");
    require(L::FORMATS[static_cast<std::size_t>(two.known().format)].team == 91, "the team it names is SSS");
    std::uint64_t const record = L::encodeDetection(one.known(), w1.round, ctx);
    require(!bot::decodeRecord(record, ctx) && !bot::decodeProbe(record, ctx) && !bot::decodeClaim(record, ctx),
            "no other decoder reads a listen record");
    test::dragon(w2, 1, false, w2.tileOf(8, 11), 2);
    bot::Belief b2;
    b2.init(w2);
    bot::Gossip g2;
    g2.detection = two.known();
    g2.plan(w2, b2, w2.head, -1);
    bool relayed = false;
    for (int dir = 0; dir < 4; dir++)
        relayed = relayed || (g2.sending[dir] && L::decodeDetection(g2.out[dir], ctx) == two.known());
    require(relayed, "dragon 2 relays it");
    g2.plan(w2, b2, w2.head, -1);
    bool again = false;
    for (int dir = 0; dir < 4; dir++)
        again = again || (g2.sending[dir] && L::decodeDetection(g2.out[dir], ctx));
    require(!again, "an unchanged detection waits P.gossipMaxAge rounds");
}

} // namespace

int main() {
    return test::suite({
        {"a real value decodes to the true head and locks no other team",
         a_real_value_decodes_to_the_true_head_and_locks_no_other_team},
        {"the detector locks onto the sender after enough values", the_detector_locks_onto_the_sender_after_enough_values},
        {"a tag recogniser names a team we cannot decode", a_tag_recogniser_names_a_team_we_cannot_decode},
        {"a family row names its members and never locks", a_family_row_names_its_members_and_never_locks},
        {"every team has an attack its recogniser takes", every_team_has_an_attack_its_recogniser_takes},
        {"an untrusted layout stores no fact", an_untrusted_layout_stores_no_fact},
        {"every spec entry round-trips", every_spec_entry_round_trips},
        {"a flipped layout reads as its report says", a_flipped_layout_reads_as_its_report_says},
        {"a detection reaches a teammate over gossip", a_detection_reaches_a_teammate_over_gossip},
    });
}
