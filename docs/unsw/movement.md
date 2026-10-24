<!-- Mirrored from https://game.battlecode.au/docs/movement on 2026-09-23. Do not edit by hand; re-run scripts/mirror_docs.py instead. -->

# Movement

Movement is considered a type of **action** that a dragon can perform on a turn. Note that each dragon
must output at least one action per turn (only the **last one** is actually performed; see [Death](death.md) for a list of death conditions). The only other type of action which can be performed is [splitting](splitting.md).

Movement, like all other unit actions, is performed only once the turn is complete.

## Standard Movement

> *[Animated board demo on the live page]* Dragons move eastwards.

Each dragon can move one tile in any cardinal direction (north, east, south, west) each turn. This causes the
head to move in said direction, with the rest of the body following along.

Dragons can even move backwards, though doing so causes its head to squeeze into its own neck, a move which
often proves fatal.

A dragon's **facing** direction is the direction of its head from its first body (non-head) segment.
The facing direction of its body segments is defined similarly (direction to the next segment towards the head).

**Python**

```python
ct.make_move(unswbc.Direction.EAST)
```

**C**

```sh
unswbc_move(UNSWBC_EAST);
```

**C++**

```cpp
ct.make_move(unswbc::Direction::EAST);
```

**Raw protocol**

```text
MOVE E
ENDTURN
```

## Eating Pearls

> *[Animated board demo on the live page]* A dragon eats a pearl and grows longer by a unit.

When a dragon moves its head onto a pearl, the pearl is automatically eaten and the tail does not advance that
turn. In other words, the dragon grows a body segment.

Eating pearls is the only way to gain length.

## Collisions

> *[Animated board demo on the live page]* Everyone dies via various means except the orange dragon.

A dragon dies if its head moves into any **obstacles**. This includes [kelp](kelp-and-portals.md), any segment of another dragon, or any segment of itself.

Note that this [death](death.md) condition is checked before any actual movement. This means a dragon
will die moving into own tail, even if the tail would have moved out of the way in the same turn.

If the destination contains another dragon's head, both dragons die, regardless of their team.

**Python**

```python
ahead = ct.get_tile(here.add_dir(direction))
part = ahead.get_dragon() if ahead is not None else None
if part is not None:
    if part.is_head():
        continue  # both of us would die
    continue      # only I would die
```

**C**

```c
UnswbcTile const *ahead = unswbc_tile(ct, unswbc_add(here, direction));
UnswbcEntity const *entity = unswbc_entity(ahead);
if (entity && entity->type == UNSWBC_ENTITY_DRAGON) {
    // entity->is_head means both of us die
    continue;
}
```

**C++**

```cpp
auto const* ahead = ct.get_tile(here.add_dir(direction));
if (auto const* part = ahead ? ahead->get_dragon() : nullptr) {
    // part->is_head() means both of us die
    continue;
}
```

A dragon that dies drops pearls along its body. See [Death](death.md) for more information.

## Sprinting

> *[Animated board demo on the live page]* Dragon decides to sprint north + north + east.

Dragons may decide to move several times in one turn in varying directions.

Although all moves happen within a single turn, collision checking (and pearl eating) is performed at every
step. This means that in theory, a dragon might die performing `NNE`, but live with `ENN`,
even though they target the same destination.

Every additional move costs a unit segment of the sprinting dragon. More rigorously, moving *x* steps in
a turn removes *x* - 1 segments.

**Python**

```python
ct.make_moves([
    unswbc.Direction.NORTH,
    unswbc.Direction.NORTH,
    unswbc.Direction.EAST,
])
```

**C**

```c
UnswbcDirection steps[] = {UNSWBC_NORTH, UNSWBC_NORTH, UNSWBC_EAST};
unswbc_moves(ct, steps, 3);
```

**C++**

```cpp
ct.make_moves({
    unswbc::Direction::NORTH,
    unswbc::Direction::NORTH,
    unswbc::Direction::EAST,
});
```

**Raw protocol**

```text
MOVE NNE
ENDTURN
```

## Death by Sprint

> *[Animated board demo on the live page]* Dragon implodes due to oversprinting.

A move with more steps than the dragon can pay for is **not** truncated. The dragon will perform the
steps that can be paid for, and then will pay for the next step with its own life.
