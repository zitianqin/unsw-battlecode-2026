<!-- Mirrored from https://game.battlecode.au/docs/structure on 2026-09-23. Do not edit by hand; re-run scripts/mirror_docs.py instead. -->

# Structure

## Rounds and Turns

Each game lasts at most **500 rounds**. Within each round, every currently living dragon will perform
some number of actions. One of these time steps is known as a **turn**.

Turns are carried out in ascending order of dragon ID. This means on a given dragon's turn, all dragons with lesser
IDs have already acted, and all dragons with greater IDs have not. For more details about the game loop, see [Execution Order](execution-order.md).

## Scoring

Each game has three possible outcomes: team A wins, team B wins, or a draw.

Your team is eliminated when all of your dragons perish. Upon the elimination of any team, the game automatically
ends, regardless of the current round number. If **exactly one team** is eliminated, the other wins. If **both teams** are eliminated in the same round, the game is a draw. Otherwise, after 500 rounds, the winner
is decided by the following tiebreaks in order:

1. The team with the longest living dragon.
2. The team with the greatest total length across all of its dragons.
3. Draw (no winning team decided).
