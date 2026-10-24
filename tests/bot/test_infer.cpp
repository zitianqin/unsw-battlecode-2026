#include "infer.hpp"
#include "support.hpp"

#include <algorithm>

using test::require;

namespace {

bool has(bot::SenderSet const& s, int t) { return std::find(s.tiles.begin(), s.tiles.end(), t) != s.tiles.end(); }

// Only the 7 x 7 window around our head (8, 8) in sight.
bot::World windowed() {
    auto w = test::world();
    w.stamp = 2;
    for (int dx = -3; dx <= 3; dx++)
        for (int dy = -3; dy <= 3; dy++)
            w.visStamp[w.tileOf(8 + dx, 8 + dy)] = w.stamp;
    return w;
}

// A sender is on a clear straight line out of one of our cells, through portals and wrapping; kelp and our own body
// end a line, and a tile we see with no enemy head on it holds no sender.
void sender_lies_on_a_clear_line_out_of_our_body() {
    auto w = windowed();
    test::body(w, {w.tileOf(8, 8), w.tileOf(7, 8), w.tileOf(6, 8)});
    test::dragon(w, 50, true, w.tileOf(8, 5), 3); // a visible enemy head north of our head
    test::wall(w, w.tileOf(7, 3), 0);             // kelp cuts column 7 on both sides of (7, 1) and (7, 2), past the view
    test::wall(w, w.tileOf(7, 1), 0);
    // A portal on the south line out of (6, 8): crossing (6, 12)'s south edge lands at (13, 3), heading south.
    w.setEdge(w.edgeOf(w.tileOf(6, 12), 2), bot::EDGE_PORTAL, 7);
    w.setEdge(w.edgeOf(w.tileOf(13, 2), 2), bot::EDGE_PORTAL, 7);
    auto const s = bot::inferSenders(w, 1);
    require(s.foreign == 1, "the unreadable values are counted");
    require(has(s, w.tileOf(8, 13)) && has(s, w.tileOf(8, 0)), "the column out of our head, unseen and wrapped");
    require(has(s, w.tileOf(12, 8)), "the row out of our head, just out of view");
    require(has(s, w.tileOf(8, 5)), "a visible enemy head on a line can be the sender");
    require(!has(s, w.tileOf(8, 6)) && !has(s, w.tileOf(10, 8)), "seen tiles with no enemy on them hold none");
    require(has(s, w.tileOf(7, 3)) && has(s, w.tileOf(7, 0)), "column 7 up to the kelp either way");
    require(!has(s, w.tileOf(7, 1)) && !has(s, w.tileOf(7, 2)), "kelp ends the lines out of (7, 8)");
    require(has(s, w.tileOf(13, 3)) && has(s, w.tileOf(13, 2)), "a known portal carries the line to its far side");
    require(!has(s, w.tileOf(12, 12)), "a tile on no line out of our body holds no sender");
}

// Lines run round the board past our other cells but never count them, and nothing is worked out when every value
// was ours.
void own_cells_are_never_senders_and_no_foreign_value_means_no_set() {
    auto w = test::world();
    std::fill(w.visStamp.begin(), w.visStamp.end(), 0); // nothing in sight: no vision prune
    test::body(w, {w.tileOf(8, 8), w.tileOf(9, 8), w.tileOf(10, 8), w.tileOf(10, 9)});
    auto const s = bot::inferSenders(w, 2);
    require(!has(s, w.tileOf(9, 8)) && !has(s, w.tileOf(10, 8)), "our own cells are never a sender");
    require(has(s, w.tileOf(0, 8)) && has(s, w.tileOf(11, 8)), "the row wraps round to our other end");
    require(has(s, w.tileOf(10, 10)) && has(s, w.tileOf(10, 7)), "the columns out of (10, 9) and (10, 8)");
    require(!has(s, w.tileOf(3, 3)), "off every line");
    require(bot::inferSenders(w, 0).tiles.empty(), "no unreadable value, no set");
}

} // namespace

int main() {
    return test::suite({
        {"sender_lies_on_a_clear_line_out_of_our_body", sender_lies_on_a_clear_line_out_of_our_body},
        {"own_cells_are_never_senders_and_no_foreign_value_means_no_set",
         own_cells_are_never_senders_and_no_foreign_value_means_no_set},
    });
}
