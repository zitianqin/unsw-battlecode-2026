// The helper reads tile lines and plain edge rows on a fast path and falls back to the general parser for
// anything else; both must read the same round. Each case feeds a whole init block and round through stdin.
#include "helper.hpp"
#include "support.hpp"

#include <string>
#include <unistd.h>

using test::require;

namespace {

// Replaces stdin with a pipe holding `text`.
void feed(std::string const& text) {
    int fds[2];
    require(pipe(fds) == 0, "pipe");
    require(write(fds[1], text.data(), text.size()) == static_cast<ssize_t>(text.size()), "write");
    close(fds[1]);
    dup2(fds[0], 0);
    close(fds[0]);
}

// One round on a 16 x 16 map with our head at (8, 8). `tileLine(x, y)` and `edgeRow(i, horizontal)` write the
// lines; the default ones are plain.
template <typename TileLine, typename EdgeRow> std::string round(TileLine tileLine, EdgeRow edgeRow) {
    std::string in = "ID 1\nTEAM A\nMAP 16 16\nUNIT_LIMIT 64\nROUND 7\nDIR N\nLENGTH 2\nUNIT_COUNT 1\nNUM_MSGS 0\n";
    for (int y = 5; y < 12; y++)
        for (int x = 5; x < 12; x++)
            in += tileLine(x, y) + "\n";
    in += "DRAGON_BODIES 2\nA 1 8 8 N 1\nA 1 8 9 N 0\n";
    for (int i = 0; i < 8; i++)
        in += edgeRow(i, true) + "\n";
    for (int i = 0; i < 7; i++)
        in += edgeRow(i, false) + "\n";
    return in;
}

std::string plainTile(int x, int y) {
    return std::to_string(x) + " " + std::to_string(y) + " " + std::to_string((x + y) % 2) + " " +
           std::to_string(x == 6 ? -1 : 10 * x + y);
}

std::string plainRow(int i, bool horizontal) {
    std::string row;
    for (int x = 0; x < (horizontal ? 7 : 8); x++)
        row += std::string(x ? " " : "") + (horizontal && i == 2 && x == 3 ? "w" : ".");
    return row;
}

// Parses `text` as one round and checks every tile against plainTile and the edges against plainRow, except
// that row 4 of horizontal edges may hold `portal` at column 1.
void requireRound(std::string const& text, int portal = -1) {
    feed(text);
    auto [ct, game] = unswbc::init();
    require(unswbc::update(ct, game), "the round is read");
    require(ct.get_tiles().size() == 49, "49 tiles");
    for (auto const& tile : ct.get_tiles()) {
        int const x = tile.position.x, y = tile.position.y;
        require(tile.has_pearl() == ((x + y) % 2 == 1), "pearl flag");
        require(tile.get_pearl_time() == (x == 6 ? -1 : 10 * x + y), "countdown, including -1");
        bool const kelpNorth = x == 8 && y == 7;
        require(tile.edges[0].is_passable() == !kelpNorth, "kelp on the north edge");
        require(tile.edges[2].is_passable() == !(x == 8 && y == 6), "the same kelp on the south side of the tile above");
        bool const portalNorth = portal >= 0 && x == 6 && y == 9;
        require(tile.edges[0].is_portal() == portalNorth, "portal only where written");
        if (portalNorth)
            require(tile.edges[0].get_portal_id() == portal, "portal id");
    }
    require(ct.get_tile(unswbc::Position(8, 8))->get_dragon() != nullptr, "our head is placed");
}

void plain_round_reads_every_value() { requireRound(round(plainTile, plainRow)); }

void odd_lines_fall_back_to_the_general_parser() {
    auto const tile = [](int x, int y) {
        std::string line = plainTile(x, y);
        if (x == 5 && y == 6)
            return "\t" + line + "  \t"; // other whitespace
        if (x == 7 && y == 6)
            return "0000000007 " + line.substr(2); // ten digits
        if (x == 9 && y == 10)
            return line + " # a comment";
        return line;
    };
    auto const row = [](int i, bool horizontal) {
        std::string line = plainRow(i, horizontal);
        if (horizontal && i == 4)
            line[2] = '9'; // a portal id in column 1
        return line;
    };
    requireRound(round(tile, row), 9);
}

void a_short_tile_line_is_an_error() {
    auto const tile = [](int x, int y) { return x == 9 && y == 9 ? std::string("9 9 0") : plainTile(x, y); };
    bool threw = false;
    try {
        requireRound(round(tile, plainRow));
    } catch (std::runtime_error const&) {
        threw = true;
    }
    require(threw, "a tile line with three values is rejected");
}

} // namespace

int main() {
    // The reader keeps its buffer between cases, so the case that stops mid-round goes last.
    return test::suite({
        {"plain round reads every value", plain_round_reads_every_value},
        {"odd lines fall back to the general parser", odd_lines_fall_back_to_the_general_parser},
        {"a short tile line is an error", a_short_tile_line_is_an_error},
    });
}
