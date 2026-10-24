<!-- Mirrored from https://game.battlecode.au/docs/overview on 2026-09-23. Do not edit by hand; re-run scripts/mirror_docs.py instead. -->

# Overview

The probe has landed at the bottom of the ocean depths, and through the camera you can see hundreds upon thousands
of pearls, scattered amongst the sea floor. Deep sea dragons live here too, so in order to blend in, you and your
team will be controlling a group of magnificent leviathan-like automata.

Each of your serpentine machines can collect pearls to grow in length; at the end of the mission, your longest robot
(and its pearls) will be extracted back to the surface.

As such, compete with other teams to stay alive and obtain the longest dragon!

## Specifications

Each team can control up to **64 dragons** at a time. Your team will write exactly one program, which
each dragon will execute, running its own **copy** of your program. A corollary of this is that
individual dragons do not share any program memory with other dragons, and hence the only way to communicate
dragon-to-dragon is via the [sonar](sonar.md) system.

Each dragon gets approximately **25 milliseconds** at most to compute and execute actions each turn.
This is not entirely correct, so for more details about computational limits for each of your dragons, see the [Timeouts](timeouts.md) section.

## Gameplay

Your resident games expert notices that the interactions between these sea dragons are similar to the rules of the
retro video game [Snake](https://simple.wikipedia.org/wiki/Snake_(video_game)), save for some
differences:

- These dragons can spontaneously [split](splitting.md) in two.
- Dragons only communicate through long-distance [sonar](sonar.md).
- A dragon can [move multiple times](movement.md) within a turn, though at a cost.

## Next Steps

- You can read about the full rules of the game starting from [Structure](structure.md).
- After that, feel free to started with your first bot at [Quickstart](quickstart.md)!
- Some more technical information about the engine and turn order starts from [IO Protocol](protocol.md).
- Once you've submitted your bot to the ladder, see [ELO System](elo.md) to understand rating and matches.
