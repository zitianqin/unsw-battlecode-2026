#pragma once

#include "harvest.hpp"
#include "params.hpp"
#include "safety.hpp"
#include "state.hpp"

#include <vector>

namespace bot {

// The learned ram gate (P.ramModel): a ram's expected swing, the victim's length net of what its team eats of the two
// drops, less ours net of what we eat (M51), as a linear model over what the rammer sees: the victim's visible length
// less ours, Safety::headEdge (every visible head, ours and the victim's included), and the other visible heads
// within P.dropEdgeRadius steps of the victim, ours and theirs apart (who eats the drop). Its weights (P.ramW*) were
// fit offline on our own rams in fresh-map games against the champion and the rotation, rule-declined ones taken on
// a hashed share of turns so declined rams have outcomes too (E0204).
inline double ramSwing(World const& w, Safety const& safety, int victim, int theirLen) {
    return headOnSwing(w, false, w.head, w.length, victim, theirLen, safety.headEdge);
}

// True when ramming the enemy head at `victim` (visible length `theirLen`) with our body lying as `body` (head first)
// at the collision is worth starting: findRam's gate and the one-step attack's score (through ramWorth), and the
// hunt's gate on an even head (hunt.hpp, along its approach). With P.evenTradeHarvest, an even ram (theirLen == our
// length) is priced by who eats its drop: the trade checks (Safety::tradeGood, at the active rule's minimum gain) and
// dropHarvestEdgeFor at least P.evenTradeMinHarvest, instead of the head counts below. Any other ram, with
// P.harvestRamGate, needs that drop edge too before the checks below. Otherwise, with P.ramModel, the
// hard trade checks (Safety::tradeGood: no protected dragon, enough units, a victim at most -P.ramModelMinGain
// shorter, the range the model was fit on) and an expected swing above P.ramModelMin; without it the hand rule,
// Safety::attackGood.
inline bool ramWorthFor(World const& w, Safety const& safety, int victim, int theirLen, std::vector<int> const& body) {
    if (P.evenTradeHarvest && theirLen == w.length)
        return safety.tradeGood(theirLen, P.ramModel ? P.ramModelMinGain : P.attackMinGain) &&
               dropHarvestEdgeFor(w, victim, body) >= P.evenTradeMinHarvest;
    // P.harvestRamGate: any other ram too only where our other heads reach more of the drop first.
    if (P.harvestRamGate && dropHarvestEdgeFor(w, victim, body) < P.evenTradeMinHarvest)
        return false;
    if (!P.ramModel)
        return safety.attackGood(theirLen);
    return safety.tradeGood(theirLen, P.ramModelMinGain) && ramSwing(w, safety, victim, theirLen) > P.ramModelMin;
}

// ramWorthFor a ram along `steps` from our head (ramBody lays our body at the collision).
inline bool ramWorth(World const& w, Safety const& safety, int victim, int theirLen, std::vector<int> const& steps) {
    return ramWorthFor(w, safety, victim, theirLen, ramBody(w, steps));
}

// True when an enemy head of visible length `theirLen` passes ramWorth's checks that don't depend on where it stands:
// findRam's cheap test before it searches.
inline bool ramCandidate(World const& w, Safety const& safety, int theirLen) {
    if (P.evenTradeHarvest && theirLen == w.length)
        return safety.tradeGood(theirLen, P.ramModel ? P.ramModelMinGain : P.attackMinGain);
    return P.ramModel ? safety.tradeGood(theirLen, P.ramModelMinGain) : safety.attackGood(theirLen);
}

} // namespace bot
