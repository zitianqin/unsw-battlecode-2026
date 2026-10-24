# Community maps

Maps players posted in the UNSW Battlecode Discord's #community-maps forum, downloaded once on 2026-09-26 with sig's
go-ahead for that one download (the loop never downloads from Discord on its own). Checked to be plain map files,
each played cleanly by the champion on both sides, then used as sources for near-variants in the `eval` style
(`mapgen/gen.py`, a ninth of eval maps), never as fixed test maps.

| File | Thread (author) | What it adds |
|---|---|---|
| portal_labyrinth.map | The Labyrinth (Benight) | a maze: 43% kelp, 8 portal pairs, x-only symmetry; the organisers asked to add it to their pool |
| great_divide.map | The Great Divide (cupcake pack) | 5 dragons a team of length 5-16 in rich pearl fields, like the Sprint maps |
| snake_trap.map | Snake Trap (RyanThe27th) | a pocket reached only by narrow corridors, few pearls outside it |
| unsw.map | unsw (grok) | 64x64 with one dragon a team and very sparse pearls |

Left out: new_map_0920 (no pearl spawns at all), ba.map (no dragons), troll.map (a 64x10 strip whose variants mostly
fail the generator's reachability check).
