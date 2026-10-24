# Adversarial maps

Maps a champion lost or drew on against the toolkit's starter bot (the `starter` entry of `referee/gauntlet.toml`),
or against the opponent its row names, kept as our own test maps. `loop/advmaps.py` searches for them once per
champion and proposes each find under `<loop home>/advmaps/<champion>/finds/`; the orchestrator commits the ones it
keeps here, a line each below. Every champion then plays each map here, both sides, as the first stage of its search,
against the bot its row's Opponent cell names (`starter`, or a `referee/gauntlet.toml` entry such as `swarm` for a
find of the search's strong pass; a blank cell is the starter; `advmaps.regression_opponents`; a name with no entry
there is recorded as a map that couldn't be played, and a row whose Opponent changes plays its map again against the
new one): any loss or draw alerts (against an opponent other than the starter, on both sides: `advmaps.lost`), and
stats.md lists the record a line a map. Nothing else plays them (the CPU gate, the identical check and the official-map
report read other folders).

| File | Opponent | What it is | Beat | Our deaths (both sides) |
|---|---|---|---|---|
| portal_labyrinth_85163986.map | starter | gen_eval_85163986, an eval-style near variant of `maps/community/portal_labyrinth.map` (32x32, y symmetry, halves joined only by portals, 1-wide corridors); found by accident on 2026-09-27 (research starterloss-2026-09-27) | every champion v0001-v0036 both sides (72 games), and v0037 (b88e1fa) both sides, all ours dead r63 as A and r69 as B | 8 hitWall, all 8 in a dead end or 1-wide corridor (v0037): our dragons walk into corridors the move scorer rates open, since unseen edges count as open |
| adv_0e962ec_c116.map | starter | the adversarial-map search's find c116 for v0038 (0e962ec), a climb mutation (portal and corridor added) of its parent c105: 46x23, xy symmetry, walled border, 10 portal pairs, 26% of tiles 1-wide, all 12 fast tiles in dead ends and 6 in corridors, some reached only through a portal | v0038 both sides (ours all dead r27 as A, r25 as B), and one or both sides on 3 of its 6 perturbations (p0-p5 in the find), p 0.68, too few for an `ADVMAPS FIND` alert at the seed pool's loss rate | 18 hitWall, all 18 in a dead end or corridor (v0038): the same unseen-edges-count-as-open fault as the portal labyrinth, on a map built by mutation rather than found |
| adv_cfe3b39_c067.map | swarm | the adversarial-map search's find c067 for v0068 (cfe3b39), a corridor mutation of c059: 32x32, x symmetry, halves cut by 1-5 moves, 20%+ of tiles 1-wide, fast tiles in corridors and dead ends and reached only through a portal; found 2026-09-29 | swarm, both sides (A r41, B r137, by elimination) and 6 of 10 perturbations (p 0.0426 corrected) | hitWall 1094 (889 in a dead end or corridor), hitOtherBody 629, hitSelf 179, hitHeadToHead 20 |
