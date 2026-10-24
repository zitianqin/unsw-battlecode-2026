#pragma once

#include "params.hpp"
#include "state.hpp"

#include <vector>

namespace bot {

// Where an enemy dragon that sonared us can be, from a value we heard but can't read (wire::open drops it). A ray
// leaves its sender's head, or its tail when sent back into its own body (docs/unsw/sonar.md), travels straight
// (wrapping, through portals) and stops at the first kelp or dragon part: ours, since we heard it. It was sent after
// our last move (a lower id this round, a higher id last round), so our body is as it was, and the sender hasn't moved
// since. So the sender's head (or tail) is on one of the four lines out of one of our body cells, before the line
// meets kelp or our own body. The engine doesn't say which cell was hit, so the set is the union over our cells; tiles
// we see with no enemy head or tail on them hold no sender. Other bodies don't cut a line: they may have moved since
// the send. A line stops at a portal whose far end we haven't seen, unseen edges count as open (World::step), and
// segments of ours we have never seen (World::body is short of our length) start no line, so the set can miss a
// sender on unknown ground or one whose ray hit an untracked segment. Past one of our own cells a line is that cell's
// own line, so stopping there only saves work.
// In 120 result-blind online games (12714-13426) the true sender's head was in the set for 95% of 65,819 values
// heard (most misses are rays sent backwards, out of the tail), and the set left 5.5% of the tiles out of view (median).
// It is the fallback for enemy positions when we can't decode their sonar: Belief::noteSenders marks its blocks enemy-likely.
struct SenderSet {
    int foreign = 0;        // values heard this turn that failed our check
    std::vector<int> tiles; // where their senders can be, when foreign > 0
};

// The set for `foreign` unreadable values heard this turn (none when foreign is 0 or P.inferSender is off).
inline SenderSet inferSenders(World const& w, int foreign) {
    SenderSet set;
    set.foreign = foreign;
    if (!P.inferSender || foreign <= 0 || w.body.empty())
        return set;
    std::vector<char> own(w.N, 0), in(w.N, 0);
    for (int t : w.body)
        own[t] = 1;
    for (int c : w.body)
        for (int d = 0; d < 4; d++) {
            int t = c;
            for (int s = 0; s < w.W + w.H; s++) {
                t = w.step(t, d);
                if (t < 0 || own[t])
                    break;
                bool const seenEmpty = w.visible(t) && !(w.occEnemy[t] && (w.occHead[t] || w.occK[t] <= 0));
                if (!seenEmpty && !in[t]) {
                    in[t] = 1;
                    set.tiles.push_back(t);
                }
            }
        }
    return set;
}

} // namespace bot
