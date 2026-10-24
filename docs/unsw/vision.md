<!-- Mirrored from https://game.battlecode.au/docs/vision on 2026-09-23. Do not edit by hand; re-run scripts/mirror_docs.py instead. -->

# Vision

Every dragon can only see the **7 by 7 square of tiles** centred on its head; that is, all 49 tiles with
a Chebyshev distance no greater than 3. This vision square wraps around the map as well due to its topology.

Again, note that vision does not extend to the far side of a portal, which is visible only if it is within three
tiles of the head by ordinary distance.

> *[Animated board demo on the live page]* A visualisation of a dragon's vision square as it moves. Note the interactions with portals and map borders.

## Vision Information

For each tile in view, the dragon receives its (absolute) coordinates, its contents (pearls and dragon parts), its
pearl countdown (see [Pearls](pearls.md)) and its four edges (see [Kelp and Portals](kelp-and-portals.md)).

**Python**

```python
tile = ct.get_tile(pos)             # None when pos is outside the window

tile.get_position()                 # board coordinates, already wrapped
tile.get_pearl_time()               # rounds until a pearl spawns here; -1 if never
tile.has_pearl()                    # True if a pearl is on it now

edge = tile.get_edge(unswbc.Direction.NORTH)
edge.get_edge_type()                # EdgeType.EMPTY, EdgeType.KELP or EdgeType.PORTAL
edge.get_portal_id()                # id shared with the partner edge; -1 if not a portal

part = tile.get_dragon()            # None if no dragon is on it
if part is not None:
    part.get_team()                 # Team.A or Team.B
    part.get_id()                   # which dragon
    part.is_head()                  # True for the head
    part.get_dir()                  # head: its heading; body: the way to the head
```

**C**

```c
UnswbcTile const* tile = unswbc_tile(ct, pos);   /* NULL when pos is outside the window */

tile->position;                                    /* board coordinates, already wrapped */
tile->pearl_time;                                  /* rounds until a pearl spawns here; -1 if never */
unswbc_has_pearl(tile);                            /* 1 if a pearl is on it now */

UnswbcEdge const* edge = unswbc_edge(tile, UNSWBC_NORTH);
unswbc_passable(edge);                             /* 0 if it is kelp */
unswbc_is_portal(edge);                            /* 1 if crossing it teleports you */
unswbc_portal_id(edge);                            /* id shared with the partner edge; -1 if not a portal */

UnswbcEntity const* entity = unswbc_entity(tile);  /* NULL on an empty tile */
if (entity && entity->type == UNSWBC_ENTITY_DRAGON)
{
    entity->team;                                  /* UNSWBC_TEAM_A or UNSWBC_TEAM_B */
    entity->dragon_id;                             /* which dragon */
    entity->is_head;                               /* true for the head */
    entity->dir;                                   /* head: its heading; body: the way to the head */
}
```

**C++**

```cpp
auto const* tile = ct.get_tile(pos);   // nullptr when pos is outside the window

tile->get_position();                   // board coordinates, already wrapped
tile->get_pearl_time();                 // rounds until a pearl spawns here; -1 if never
tile->has_pearl();                      // true if a pearl is on it now

auto const& edge = tile->get_edge(unswbc::Direction::NORTH);
edge.is_passable();                     // false if it is kelp
edge.is_portal();                       // true if crossing it teleports you
edge.get_portal_id();                   // id shared with the partner edge; -1 if not a portal

if (auto const* part = tile->get_dragon())   // nullptr if no dragon is on it
{
    part->get_team();                   // Team::A or Team::B
    part->get_id();                     // which dragon
    part->is_head();                    // true for the head
    part->get_dir();                    // head: its heading; body: the way to the head
}
```

A turn that steps onto a neighbouring pearl when it can see one, and onto a tile with no dragon on it otherwise:

**Python**

```python
def execute_turn() -> None:
    here = ct.get_position()
    fallback = unswbc.Direction.NORTH

    for direction in unswbc.Direction.get_direction_list():
        tile = ct.get_tile(here.add_dir(direction))
        if tile is None:
            continue
        if tile.has_pearl():
            ct.make_move(direction)
            return
        if tile.get_dragon() is None:
            fallback = direction

    ct.make_move(fallback)
```

**C**

```c
UnswbcPosition here = unswbc_position(ct);
UnswbcDirection fallback = UNSWBC_NORTH;

for (int i = 0; i < 4; i++)
{
    UnswbcDirection direction = UNSWBC_DIRECTIONS[i];
    UnswbcTile const* tile = unswbc_tile(ct, unswbc_add(here, direction));
    if (!tile)
    {
        continue;
    }

    if (unswbc_has_pearl(tile))
    {
        unswbc_move(direction);
        return;
    }

    UnswbcEntity const* entity = unswbc_entity(tile);
    if (!entity || entity->type != UNSWBC_ENTITY_DRAGON)
    {
        fallback = direction;
    }
}

unswbc_move(fallback);
```

**C++**

```cpp
auto const here = ct.get_position();
unswbc::Direction fallback = unswbc::Direction::NORTH;

for (auto const direction : unswbc::Direction::get_direction_list())
{
    auto const* tile = ct.get_tile(here.add_dir(direction));
    if (!tile)
    {
        continue;
    }

    if (tile->has_pearl())
    {
        ct.make_move(direction);
        return;
    }
    if (!tile->get_dragon())
    {
        fallback = direction;
    }
}

ct.make_move(fallback);
```
