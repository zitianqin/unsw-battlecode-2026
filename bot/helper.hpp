#pragma once

#include <algorithm>
#include <array>
#include <charconv>
#include <cstdint>
#include <cerrno>
#include <cstdio>
#include <cstring>
#include <iostream>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <tuple>
#include <type_traits>
#include <utility>
#include <vector>

#include <unistd.h>

namespace unswbc {

// Limits the engine holds every game to.
struct Constants {
    static constexpr int MAX_ROUNDS = 500;
    static constexpr int VISION_RADIUS = 3;
    static constexpr int VISION_SIZE = 2 * VISION_RADIUS + 1;
    static constexpr int INITIAL_LENGTH = 3;
    static constexpr int MIN_SIZE = 2;
    static constexpr int PROTOCOL_MAJOR = 3;
};

// The protocol's direction letters.
class Direction {
  public:
    enum Value : char {
        NORTH = 'N',
        EAST = 'E',
        SOUTH = 'S',
        WEST = 'W'
    };
    Value value;

    // Throws if the letter is not one of N, E, S or W.
    Direction(Value value) : value(value) {
        if (value != NORTH && value != EAST && value != SOUTH && value != WEST)
            throw std::invalid_argument("invalid direction");
    }
    Direction(char value) : Direction(static_cast<Value>(value)) {}
    constexpr bool operator==(Direction const&) const = default;

    // North, east, south and west, in that order.
    static std::array<Direction, 4> get_direction_list() {
        static std::array<Direction, 4> const direction_list{NORTH, EAST, SOUTH, WEST};
        return direction_list;
    }

    // The step this direction makes, as dx east and dy south.
    std::pair<int, int> get_offset() const {
        switch (value) {
        case NORTH: return {0, -1};
        case EAST:  return {1, 0};
        case SOUTH: return {0, 1};
        default:    return {-1, 0};
        }
    }

    Direction get_opposite() const {
        return value == NORTH ? Direction(SOUTH)
             : value == EAST  ? Direction(WEST)
             : value == SOUTH ? Direction(NORTH)
                              : Direction(EAST);
    }
    Direction get_left() const {
        return value == NORTH ? Direction(WEST)
             : value == EAST  ? Direction(NORTH)
             : value == SOUTH ? Direction(EAST)
                              : Direction(SOUTH);
    }
    Direction get_right() const {
        return value == NORTH ? Direction(EAST)
             : value == EAST  ? Direction(SOUTH)
             : value == SOUTH ? Direction(WEST)
                              : Direction(NORTH);
    }
};

inline std::ostream& operator<<(std::ostream& stream, Direction direction) {
    return stream << direction.value;
}

// The protocol's team letters.
class Team {
  public:
    enum Value : char {
        A = 'A',
        B = 'B'
    };
    Value value;

    // Throws if the letter is not A or B.
    Team(Value value) : value(value) {
        if (value != A && value != B)
            throw std::invalid_argument("invalid team");
    }
    Team(char value) : Team(static_cast<Value>(value)) {}
    constexpr bool operator==(Team const&) const = default;

    Team get_enemy_team() const {
        return value == B ? A : B;
    }
};

class Game;
class Controller;

inline Game* game = nullptr;
inline Controller* ct = nullptr;

// Board coordinates, with y growing south from 0 at the top.
struct Position {
    int x = 0, y = 0;

    Position() = default;
    Position(int x, int y) : x(x), y(y) {}
    constexpr bool operator==(Position const&) const = default;

    // The tile one step away, wrapped around the map.
    Position add_dir(Direction direction) const;
    bool is_in_map() const;
    // True if the position is inside the dragon's window this turn.
    bool is_in_vision() const;
};

// The board, and the round being played.
class Game {
  public:
    int round_num = 1;
    int width, height;
    int unit_limit;

    Game(int width, int height, int unit_limit) : width(width), height(height), unit_limit(unit_limit) {}

    int get_round_num() const { return round_num; }
    // Width then height.
    std::pair<int, int> get_map_size() const { return {width, height}; }
    int get_unit_limit() const { return unit_limit; }
};

inline Position Position::add_dir(Direction direction) const {
    if (!game)
        throw std::runtime_error("game is not initialised");
    auto const [dx, dy] = direction.get_offset();
    auto const wrap = [](int value, int size) { return (value % size + size) % size; };
    return {wrap(x + dx, game->width), wrap(y + dy, game->height)};
}

inline bool Position::is_in_map() const {
    if (!game)
        throw std::runtime_error("game is not initialised");
    return x >= 0 && y >= 0 && x < game->width && y < game->height;
}

// A segment of a dragon, standing on a tile.
class DragonPart {
  public:
    Position position;
    int dragon_id;
    Team team;
    Direction dir;
    bool is_dragon_head;

    DragonPart(Position position, int dragon_id, Team team, Direction direction, bool is_dragon_head)
        : position(position), dragon_id(dragon_id), team(team), dir(direction), is_dragon_head(is_dragon_head) {}

    Position get_position() const { return position; }
    int get_id() const { return dragon_id; }
    Team get_team() const { return team; }
    // The way this segment faces.
    // A head's facing is its heading, a body segment's points towards the head.
    Direction get_dir() const { return dir; }
    bool is_head() const { return is_dragon_head; }
};

// What lies on the edge between two tiles.
enum class EdgeType {
    EMPTY,
    KELP,
    PORTAL,
};

// One side of a tile, which a dragon crosses to leave it.
class Edge {
  public:
    bool is_horizontal = false;
    EdgeType edge_type = EdgeType::EMPTY;
    std::optional<int> portal_id;

    Edge() = default;
    Edge(bool is_horizontal, EdgeType edge_type, std::optional<int> portal_id = std::nullopt)
        : is_horizontal(is_horizontal), edge_type(edge_type), portal_id(portal_id) {}

    // False for kelp.
    bool is_passable() const { return edge_type != EdgeType::KELP; }
    // True if crossing the edge teleports the dragon to the partner edge.
    bool is_portal() const { return edge_type == EdgeType::PORTAL; }
    EdgeType get_edge_type() const { return edge_type; }
    // -1 when the edge is not a portal.
    int get_portal_id() const { return portal_id.value_or(-1); }
};

// One of the 49 tiles the dragon can see this turn.
class Tile {
  public:
    Position position;
    int pearl_time = 0;
    bool pearl = false;
    std::optional<DragonPart> dragon_part;
    std::array<Edge, 4> edges{}; // {NORTH, EAST, SOUTH, WEST}

    Tile(Position position, std::optional<DragonPart> dragon_part = std::nullopt, int pearl_time = 0, bool has_pearl = false,
         std::array<Edge, 4> edges = {})
        : position(position), pearl_time(pearl_time), pearl(has_pearl), dragon_part(std::move(dragon_part)),
          edges(std::move(edges)) {}

    Edge const& get_edge(Direction direction) const {
        return edges[get_direction_index(direction)];
    }
    Edge& get_edge(Direction direction) {
        return edges[get_direction_index(direction)];
    }
    // Null if no dragon segment is on the tile.
    DragonPart const* get_dragon() const {
        return dragon_part.has_value() ? &*dragon_part : nullptr;
    }
    DragonPart* get_dragon() {
        return dragon_part.has_value() ? &*dragon_part : nullptr;
    }
    bool has_pearl() const { return pearl; }
    // Rounds until a pearl tries to spawn; -1 if it never does.
    int get_pearl_time() const { return pearl_time; }
    Position get_position() const { return position; }

  private:
    static std::size_t get_direction_index(Direction direction) {
        switch (direction.value) {
        case Direction::NORTH: return 0;
        case Direction::EAST:  return 1;
        case Direction::SOUTH: return 2;
        default:               return 3;
        }
    }
};

// The 7x7 window of tiles the dragon can see.
class Vision {
  public:
    std::vector<Tile> tiles;

    // Throws if two tiles share a position.
    Vision(std::vector<Tile> tiles = {}) : tiles(std::move(tiles)) {
        for (std::size_t index = 0; index < this->tiles.size(); index++)
            for (std::size_t other = 0; other < index; other++)
                if (this->tiles[other].position == this->tiles[index].position)
                    throw std::runtime_error("duplicate tile position");
    }

    // Row by row, starting three tiles north and three west of the head.
    std::vector<Tile> const& get_tiles() const { return tiles; }

    // Null outside the 7x7 window.
    Tile const* get_tile(Position position) const {
        for (Tile const& tile : tiles)
            if (tile.position == position)
                return &tile;
        return nullptr;
    }
    Tile* get_tile(Position position) {
        for (Tile& tile : tiles)
            if (tile.position == position)
                return &tile;
        return nullptr;
    }
};

struct SonarEchoes {
    int kelp = 0;
    int ally = 0;
    int ally_head = 0;
    int enemy = 0;
    int enemy_head = 0;
};

// What the dragon knows about itself and what it can see this turn.
class Controller {
  public:
    int length = Constants::INITIAL_LENGTH;
    int unit_count = 1;
    int unit_limit;
    DragonPart head;
    Vision vision;
    std::vector<std::uint64_t> sonar_messages;
    SonarEchoes sonar_echoes;

    Controller(int dragon_id, Team team, Direction direction, Vision vision, int unit_limit,
               std::vector<std::uint64_t> sonar_messages = {})
        : unit_limit(unit_limit), head(Position(0, 0), dragon_id, team, direction, true),
          vision(std::move(vision)), sonar_messages(std::move(sonar_messages)) {}

    // Segments, head included.
    int get_length() const { return length; }
    // Living dragons on this dragon's team.
    int get_unit_count() const { return unit_count; }
    DragonPart& get_head() { return head; }
    DragonPart const& get_head() const { return head; }
    // Unique within the game, and a split child gets the next one.
    int get_id() const { return head.dragon_id; }
    Team get_team() const { return head.team; }
    Direction get_dir() const { return head.dir; }
    // Where the dragon's head is.
    Position get_position() const { return head.get_position(); }

    Vision& get_vision() { return vision; }
    Vision const& get_vision() const { return vision; }

    std::vector<Tile> const& get_tiles() const { return vision.get_tiles(); }
    // Null outside the 7x7 window.
    Tile const* get_tile(Position position) const { return vision.get_tile(position); }
    Tile* get_tile(Position position) { return vision.get_tile(position); }

    // The last action printed in a turn is the one the engine applies.
    void make_move(Direction direction) {
        char const line[] = {'M', 'O', 'V', 'E', ' ', direction.value, '\n'};
        std::cout.write(line, sizeof line);
    }
    // One tile per direction, all in this turn.
    // The helper sends whatever you pass, and a sprint of n steps costs n-1
    // segments, so the dragon must be longer than n.
    void make_moves(std::vector<Direction> const& directions) {
        std::cout << "MOVE ";
        for (Direction direction : directions)
            std::cout << direction.value;
        std::cout << "\n";
    }
    // True if child_size segments can be split off this turn.
    bool can_split(int child_size) const {
        return Constants::MIN_SIZE <= child_size && length - child_size >= Constants::MIN_SIZE && unit_count < unit_limit;
    }
    // Splits child_size segments off the tail as a new dragon.
    // The child is those segments reversed, and takes its own turn later in
    // the same round.
    void do_split(int child_size) {
        std::cout << "SPLIT " << child_size << "\n";
    }
    // Attaches a message to this turn, which the replay shows.
    // Each line costs points, see the Timeouts page.
    template <typename... Messages> void output_log(Messages const&... messages) {
        std::cout << "LOG";
        ((std::cout << " " << messages), ...);
        std::cout << "\n";
    }
    // Draws a dot on the board in the replay.
    void draw_indicator_dot(Position position, int r, int g, int b) {
        std::cout << "DOT " << position.x << " " << position.y << " " << r << " " << g << " " << b << "\n";
    }
    // Draws a line on the board in the replay.
    void draw_indicator_line(Position start, Position end, int r, int g, int b) {
        std::cout << "LINE " << start.x << " " << start.y << " " << end.x << " " << end.y << " " << r << " " << g << " " << b << "\n";
    }
    // Labels the dragon for this turn in the replay.
    void set_indicator_string(std::string_view message) {
        std::cout << "INDICATOR " << message << "\n";
    }
    // This turn's messages, in the order they were sent.
    std::vector<std::uint64_t> get_sonar_messages() const { return sonar_messages; }
    SonarEchoes get_sonar_echoes() const { return sonar_echoes; }
    // Formatted by hand: a stream's number formatting costs thousands of points a value.
    void send_sonar(Direction direction, std::uint64_t message) {
        char line[32] = {'S', 'O', 'N', 'A', 'R', ' ', static_cast<char>(direction.value), ' '};
        char* end = std::to_chars(line + 8, line + sizeof line - 1, message).ptr;
        *end++ = '\n';
        std::cout.write(line, end - line);
    }
    bool send_sonar(std::uint64_t message) {
        if (message > UINT32_MAX)
            return false;
        std::cout << "SONAR " << message << "\n";
        return true;
    }
};

inline bool Position::is_in_vision() const {
    if (!game || !ct)
        throw std::runtime_error("game and controller are not initialised");
    if (!is_in_map())
        return false;
    Position const centre = ct->get_position();
    auto const wrap = [](int value, int size) { return (value % size + size) % size; };
    int const column = wrap(x - (centre.x - Constants::VISION_RADIUS), game->width);
    int const row = wrap(y - (centre.y - Constants::VISION_RADIUS), game->height);
    return column < Constants::VISION_SIZE && row < Constants::VISION_SIZE;
}

namespace parse_util {

inline std::unique_ptr<Controller> controller_storage;
inline std::unique_ptr<Game> game_storage;

// Reads the controller's input a line at a time from its own buffer, filled by read(2) only when no whole
// line is waiting, so it never blocks on input the engine has not sent. next_line() moves to the next line
// that holds any values ('#' starts a comment); word() and value() then take its values in order.
class Reader {
  public:
    void next_line() {
        for (;;) {
            char const* newline = nullptr;
            while (!(newline = static_cast<char const*>(std::memchr(data.data() + start, '\n', filled - start)))) {
                if (!fill()) {
                    if (start == filled)
                        throw std::runtime_error("unexpected end of controller input");
                    newline = data.data() + filled; // the last line has no newline
                    break;
                }
            }
            at = data.data() + start;
            end = newline;
            start = std::min(filled, static_cast<std::size_t>(newline - data.data()) + 1);
            if (void const* comment = std::memchr(at, '#', static_cast<std::size_t>(end - at)))
                end = static_cast<char const*>(comment);
            if (!done())
                return;
        }
    }

    // True once the current line has no values left.
    bool done() {
        while (at < end && space(*at))
            at++;
        return at == end;
    }

    // The current line's next value; throws if there is none.
    std::string_view word() {
        if (done())
            throw std::runtime_error("missing value");
        char const* const first = at;
        while (at < end && !space(*at))
            at++;
        return std::string_view(first, static_cast<std::size_t>(at - first));
    }

    template <typename T> T value();

    // The fast path for a line of `count` plain integers (at most 9 digits, maybe '-', whitespace between): reads
    // them into `out` and the whole line, or returns false and reads nothing when the rest of the line is anything
    // else, so the caller can parse it with word() and value(), which read the same values.
    bool ints(int* out, int count) {
        char const* p = at;
        char const* const e = end;
        for (int i = 0; i < count; i++) {
            while (p < e && space(*p))
                p++;
            bool const negative = p < e && *p == '-';
            char const* const digits = p + negative;
            int value = 0;
            for (p = digits; p < e && static_cast<unsigned>(*p - '0') < 10; p++)
                value = value * 10 + (*p - '0');
            if (p == digits || p - digits > 9 || (p < e && !space(*p)))
                return false;
            out[i] = negative ? -value : value;
        }
        while (p < e && space(*p))
            p++;
        if (p != e)
            return false;
        at = p;
        return true;
    }

    // The fast path for an edge row of `count` one-letter symbols ('.' open, 'w' kelp): sets kelp[i], reads the
    // line and returns true; returns false and reads nothing when the line holds anything else (a portal id).
    bool marks(bool* kelp, int count) {
        char const* p = at;
        char const* const e = end;
        for (int i = 0; i < count; i++) {
            while (p < e && space(*p))
                p++;
            if (p == e || (*p != '.' && *p != 'w') || (p + 1 < e && !space(p[1])))
                return false;
            kelp[i] = *p == 'w';
            p++;
        }
        while (p < e && space(*p))
            p++;
        if (p != e)
            return false;
        at = p;
        return true;
    }

    // Throws unless the current line has no values left.
    void finish(char const* what) {
        if (!done())
            throw std::runtime_error(std::string(what) + ": too many values");
    }

  private:
    std::vector<char> data = std::vector<char>(1 << 16);
    std::size_t start = 0, filled = 0; // unread input is data[start, filled)
    char const* at = nullptr;          // the current line: [at, end)
    char const* end = nullptr;

    static bool space(char c) { return c == ' ' || (c >= '\t' && c <= '\r'); }

    // Appends whatever input is ready (waiting for some). False at the end of input.
    bool fill() {
        if (start > 0) {
            std::memmove(data.data(), data.data() + start, filled - start);
            filled -= start;
            start = 0;
        }
        if (filled == data.size())
            data.resize(2 * data.size());
        for (;;) {
            ssize_t const got = ::read(0, data.data() + filled, data.size() - filled);
            if (got > 0) {
                filled += static_cast<std::size_t>(got);
                return true;
            }
            if (got == 0)
                return false;
            if (errno != EINTR)
                throw std::runtime_error("cannot read controller input");
        }
    }
};

inline Reader reader;

// Moves to the next line and checks it starts with `label`.
inline void expect_label(std::string_view label) {
    reader.next_line();
    std::string_view const found = reader.word();
    if (found != label)
        throw std::runtime_error("expected " + std::string(label) + ", got " + std::string(found));
}

template <typename T> T parse_integer(std::string_view text) {
    // Short decimals (every coordinate, flag and count) are read directly; from_chars handles the rest.
    if (std::is_signed_v<T> && !text.empty() && text.size() <= 9) {
        std::size_t const start = text[0] == '-' ? 1 : 0;
        T value = 0;
        bool digits = start < text.size();
        for (std::size_t at = start; digits && at < text.size(); at++) {
            unsigned const digit = static_cast<unsigned>(text[at] - '0');
            digits = digit < 10;
            value = value * 10 + static_cast<T>(digit);
        }
        if (digits)
            return start ? -value : value;
    }
    T value{};
    auto const [end, error] = std::from_chars(text.data(), text.data() + text.size(), value);
    if (error != std::errc{} || end != text.data() + text.size())
        throw std::runtime_error("invalid integer: " + std::string(text));
    return value;
}

template <typename T> T Reader::value() { return parse_integer<T>(word()); }

// The common case read in one pass: up to 9 digits, maybe signed. Anything else goes through word().
template <> inline int Reader::value<int>() {
    if (done())
        throw std::runtime_error("missing value");
    char const* next = at + (*at == '-' ? 1 : 0);
    char const* const digits = next;
    int result = 0;
    for (; next < end; next++) {
        unsigned const digit = static_cast<unsigned>(*next - '0');
        if (digit >= 10)
            break;
        result = result * 10 + static_cast<int>(digit);
    }
    if (next == digits || next - digits > 9 || (next < end && !space(*next)))
        return parse_integer<int>(word());
    bool const negative = digits != at;
    at = next;
    return negative ? -result : result;
}

// A line holding `label` and one value.
template <typename T> T read_labelled(std::string_view label) {
    expect_label(label);
    T const result = reader.value<T>();
    reader.finish(std::string(label).c_str());
    return result;
}

inline Edge parse_edge(std::string_view value, bool is_horizontal) {
    if (value == ".")
        return Edge(is_horizontal, EdgeType::EMPTY);
    if (value == "w")
        return Edge(is_horizontal, EdgeType::KELP);
    int const portal_id = parse_integer<int>(value);
    if (portal_id < 0)
        throw std::runtime_error("portal IDs cannot be negative");
    return Edge(is_horizontal, EdgeType::PORTAL, portal_id);
}

inline char read_char_safe(std::string_view str) {
    if (str.empty())
        throw std::runtime_error("expected non-empty character token");
    return str.front();
}

// tile index (y * width + x) -> this turn's vision tile standing there, -1 for none; update() clears it.
inline std::vector<int> tile_slot;

} // end namespace parse_util

// Reads the spawn block. The full buffer it sets is what makes a turn's
// output cost one write.
inline std::tuple<Controller&, Game&> init() {
    // The judge's stdout looks like a terminal, so it is line buffered unless
    // we ask otherwise, and every line costs a write. One buffer, one write a
    // turn, flushed by end_turn. Leave sync_with_stdio alone: it is what puts
    // std::cout through this buffer.
    std::setvbuf(stdout, nullptr, _IOFBF, 1 << 16);
    auto& in = parse_util::reader;
    int const dragon_id = parse_util::read_labelled<int>("ID");
    parse_util::expect_label("TEAM");
    Team const team(parse_util::read_char_safe(in.word()));
    in.finish("TEAM");

    parse_util::expect_label("MAP");
    int const width = in.value<int>();
    int const height = in.value<int>();
    in.finish("MAP");
    if (width <= 0 || height <= 0)
        throw std::runtime_error("map dimensions must be positive");
    int const unit_limit = parse_util::read_labelled<int>("UNIT_LIMIT");
    if (unit_limit <= 0)
        throw std::runtime_error("UNIT_LIMIT must be positive");

    parse_util::tile_slot.assign(static_cast<std::size_t>(width) * height, -1);
    parse_util::game_storage = std::make_unique<Game>(width, height, unit_limit);
    parse_util::controller_storage = std::make_unique<Controller>(dragon_id, team, Direction::NORTH, Vision{}, unit_limit);
    ct = parse_util::controller_storage.get();
    game = parse_util::game_storage.get();
    return {*ct, *game};
}

// Reads the next turn into the controller. False once the game is over or the
// dragon has died.
inline bool update(Controller& controller, Game& game_state) {
    auto& in = parse_util::reader;
    in.next_line();
    if (in.word() == "ENDGAME")
        return false;
    game_state.round_num = in.value<int>();
    in.finish("ROUND");

    parse_util::expect_label("DIR");
    Direction const current_direction(parse_util::read_char_safe(in.word()));
    in.finish("DIR");
    controller.head.dir = current_direction;
    controller.length = parse_util::read_labelled<int>("LENGTH");
    controller.unit_count = parse_util::read_labelled<int>("UNIT_COUNT");
    if (controller.unit_count <= 0 || controller.unit_count > controller.unit_limit)
        throw std::runtime_error("UNIT_COUNT must be between 1 and UNIT_LIMIT");

    int const num_sonar_msgs = parse_util::read_labelled<int>("NUM_MSGS");
    if (num_sonar_msgs < 0)
        throw std::runtime_error("NUM_MSGS cannot be negative");
    controller.sonar_messages.clear();
    controller.sonar_messages.reserve(static_cast<std::size_t>(num_sonar_msgs));
    for (int index = 0; index < num_sonar_msgs; index++) {
        in.next_line();
        controller.sonar_messages.push_back(in.value<std::uint64_t>());
        in.finish("sonar message");
    }

    // The first vision tile's line, after the optional echoes line.
    in.next_line();
    std::string_view first = in.word();
    controller.sonar_echoes = {};
    if (first == "ECHOES") {
        controller.sonar_echoes = {in.value<int>(), in.value<int>(), in.value<int>(), in.value<int>(), in.value<int>()};
        in.finish("ECHOES");
        in.next_line();
        first = in.word();
    }

    // Tiles are found by position through tile_slot, which is cleared again however this turn ends.
    std::vector<Tile>& tiles = controller.vision.tiles;
    auto const slot_of = [&](Position position) -> int* {
        if (position.x < 0 || position.y < 0 || position.x >= game_state.width || position.y >= game_state.height)
            return nullptr;
        return &parse_util::tile_slot[static_cast<std::size_t>(position.y) * game_state.width + position.x];
    };
    for (Tile const& tile : tiles)
        if (int* const at = slot_of(tile.position))
            *at = -1;
    tiles.clear();
    for (int index = 0; index < Constants::VISION_SIZE * Constants::VISION_SIZE; index++) {
        int values[4];
        if (index > 0)
            in.next_line();
        if (index == 0 || !in.ints(values, 4)) {
            if (index > 0)
                first = in.word();
            values[0] = parse_util::parse_integer<int>(first);
            for (int i = 1; i < 4; i++)
                values[i] = in.value<int>();
            in.finish("vision tile");
        }
        int const x = values[0], y = values[1], has_pearl = values[2], pearl_time = values[3];
        if (has_pearl != 0 && has_pearl != 1)
            throw std::runtime_error("hasPearl must be 0 or 1");

        Position const position(x, y);
        int* const at = slot_of(position);
        if (!at)
            throw std::runtime_error("vision tile is outside the map");
        if (*at >= 0)
            throw std::runtime_error("duplicate vision tile");

        *at = static_cast<int>(tiles.size());
        tiles.emplace_back(position, std::nullopt, pearl_time, has_pearl == 1);
    }

    int const num_dragon_parts = parse_util::read_labelled<int>("DRAGON_BODIES");
    if (num_dragon_parts < 0)
        throw std::runtime_error("DRAGON_BODIES cannot be negative");

    bool found_controller_head = false;
    for (int index = 0; index < num_dragon_parts; index++) {
        in.next_line();
        Team const team(parse_util::read_char_safe(in.word()));
        int const dragon_id = in.value<int>();
        int const part_x = in.value<int>();
        Position const position(part_x, in.value<int>());
        Direction const facing(parse_util::read_char_safe(in.word()));
        int const head_flag = in.value<int>();
        in.finish("dragon part");
        if (head_flag != 0 && head_flag != 1)
            throw std::runtime_error("isHead must be 0 or 1");

        int const* const tile = slot_of(position);
        if (!tile || *tile < 0)
            throw std::runtime_error("dragon part is outside the vision tiles");

        DragonPart part(position, dragon_id, team, facing, head_flag == 1);
        if (dragon_id == controller.get_id() && head_flag == 1) {
            controller.head = part;
            controller.head.dir = current_direction;
            found_controller_head = true;
        }
        tiles[*tile].dragon_part = part;
    }
    if (!found_controller_head)
        throw std::runtime_error("controller head is missing from DRAGON_BODIES");

    // The window's tiles by local position, looked up once for the edge rows below.
    Position const head_position = controller.get_position();
    auto const wrap = [](int value, int size) { return (value % size + size) % size; };
    std::array<Tile*, Constants::VISION_SIZE * Constants::VISION_SIZE> window;
    for (int local_y = 0; local_y < Constants::VISION_SIZE; local_y++) {
        int const y = wrap(head_position.y + local_y - Constants::VISION_RADIUS, game_state.height);
        for (int local_x = 0; local_x < Constants::VISION_SIZE; local_x++) {
            Position const position(wrap(head_position.x + local_x - Constants::VISION_RADIUS, game_state.width), y);
            int const* const found = slot_of(position);
            if (!found || *found < 0)
                throw std::runtime_error("vision is missing expected tile");
            window[local_y * Constants::VISION_SIZE + local_x] = &tiles[*found];
        }
    }
    auto const tile_at = [&](int local_x, int local_y) -> Tile& { return *window[local_y * Constants::VISION_SIZE + local_x]; };

    for (int y = 0; y < Constants::VISION_SIZE + 1; y++) {
        in.next_line();
        bool kelp[Constants::VISION_SIZE];
        bool const plain = in.marks(kelp, Constants::VISION_SIZE);
        for (int x = 0; x < Constants::VISION_SIZE; x++) {
            Edge edge = plain ? Edge(true, kelp[x] ? EdgeType::KELP : EdgeType::EMPTY)
                              : parse_util::parse_edge(in.word(), true);
            if (y > 0)
                tile_at(x, y - 1).edges[2] = edge;
            if (y < Constants::VISION_SIZE)
                tile_at(x, y).edges[0] = edge;
        }
        in.finish("horizontal edge row");
    }

    for (int y = 0; y < Constants::VISION_SIZE; y++) {
        in.next_line();
        bool kelp[Constants::VISION_SIZE + 1];
        bool const plain = in.marks(kelp, Constants::VISION_SIZE + 1);
        for (int x = 0; x < Constants::VISION_SIZE + 1; x++) {
            Edge edge = plain ? Edge(false, kelp[x] ? EdgeType::KELP : EdgeType::EMPTY)
                              : parse_util::parse_edge(in.word(), false);
            if (x > 0)
                tile_at(x - 1, y).edges[1] = edge;
            if (x < Constants::VISION_SIZE)
                tile_at(x, y).edges[3] = edge;
        }
        in.finish("vertical edge row");
    }
    return true;
}

// Ends the turn and flushes, which is the one write a turn costs.
inline void end_turn() {
    static_assert(Constants::PROTOCOL_MAJOR == 3, "the line below asks for protocol 3");
    static constexpr char line[] = "PROTOCOL 3\nENDTURN\n";
    std::cout.write(line, sizeof line - 1);
    std::cout.flush();
}

} // end namespace unswbc