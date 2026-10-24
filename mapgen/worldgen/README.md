# worldgen (vendored)

`worldgen.py` is another competitor's map generator, copied unchanged from the gist
https://gist.github.com/xCirno1/ffdaac4236c1f1085c351af4fdfc1600 at version
2d49ffd7dba611fbfe8b27e5d770a90359ee34ec (sha256 of the file
7134bfe6cd24e5efff48a277b618db5618dea569dce6e600bbeb8a93e2673ee2). sig allowed its use on 2026-09-28, for this
pinned version. It was read in full before it first ran: standard library only, and its one file write is the CLI's
`-o`/`--outdir`. Never edit it: `mapgen/gen.py`'s `worldgen` style wraps it (`worldgen_map`), and a newer version is
a new copy with its own read. `mapgen/test_gen.py` checks the file's hash.

Its own README, unchanged:

---

# worldgen.py — procedural Battlecode dragon maps

A single-file, standard-library-only generator for symmetric maps in the
`.map` format (`MAP w h`, `SYMMETRY`, `TILE x y lo hi`, `EDGE idx kind pid`,
`DRAGON team n x y ...`). It builds maps layer by layer, and each stage reads
what the earlier ones decided:

1. **climate**: periodic fBm elevation and fertility, symmetrised
2. **landmark**: one map-scale structure, planned before the camps so they
   settle round it:
   - citadel: concentric curtain walls with staggered gates
   - great labyrinth, or a dense 1-wide warren with chambers
   - palace
   - walled city
   - serpent hall: a diagonal lane field
   - great wall
   - parallel lanes
   - line-art effigies, with crowns and words in kelp strokes
   - portal lattice: sealed pearl boxes linked by portals
3. **districts and biomes**: camps and a heart on the axis, then warped
   Voronoi regions, each given a biome by its role and the climate
4. **structures**:
   - gated borders
   - cave grottoes (cellular automata)
   - buildings by biome: rooms, BSP complexes, cloisters, stalls,
     colonnades, labyrinths, ramparts, stockades
   - spiral and zig-zag nests the first dragon starts coiled in
   - elevation ridges
   - portal shrines and wormholes
5. **repair**: every pocket gets a door, so no sealed rooms and no rock
6. **economy**: one gap palette per map, scaled to a pearl supply target
7. **spawns**, then a **judge** that keeps the best of N candidates. It scores
   contested supply, first contact, detours, dead ends, early food, portal use
   and structure.

Every write is mirrored, so maps are exactly symmetric (`x`, `y` or `xy`) by
construction, and a seed always reproduces the same map.

```
python worldgen.py -o world.map                      # a random world
python worldgen.py --seed 42 -o pg_42.map
python worldgen.py --seed 31 --size 63x27 --sym xy --border closed --landmark serpent -o serpent.map
python worldgen.py --style labyrinth --landmark labyrinth --size 48x24 -o maze.map
python worldgen.py --batch 6 --outdir maps/
```

Options: `--style {archipelago,caverns,fortress,labyrinth,shrines,wilds}`,
`--landmark {citadel,labyrinth,palace,city,serpent,wall,lanes,effigy,lattice,none}`,
`--size WxH`, `--sym {x,y,xy}`, `--border {closed,wrap}`, `--dragons N`
(per side), `--supply P` (pearls per round), `--candidates N`.

Without `-o` it prints the world's summary and writes nothing.

As a library, `generate(seed, candidates=8, **kw)` returns a `World`.
`.text()` gives the `.map` file and `.summary()` a short report.

```python
from worldgen import generate
open('serpent.map', 'w').write(generate(31, w=63, h=27, sym='xy', landmark='serpent').text())
```
