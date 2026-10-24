# Rules for editing the bot

This folder is our UNSW Battlecode bot: C++20, one program, and every dragon runs its own copy as a separate
process. Automated experiments change it one idea at a time, and a referee keeps a change only if it wins
games. These rules keep the code workable for the next experiment.

- **Touch only `bot/`.** Anything else in the repo belongs to the referee or the loop, and a change there
  rejects the experiment.
- **New behaviour goes in a new, clearly named module or function.** Wire it in from the move-choice code.
  Don't smear it across existing code.
- **Refactors must not change behaviour.** If you restructure, the bot must play the same moves as before.
- **Numbers live in `params.hpp`.** Give every new constant a name and a default there, with a comment on what
  it controls. Don't hand-tune values; the tuner does that.
- **Deterministic.** No randomness in decisions, and ties broken by a fixed order (score, then position, then
  id), so identical copies of the program agree without messages.
- **CPU.** A turn has 100M points and going over kills the dragon. Keep p99 under 50M on the biggest maps
  (`.venv/bin/python loop/quick.py` checks this). A split child pays its own setup on its first turn, so do heavy work
  lazily, never all at once.
- **Output.** The helper flushes once per turn. Never add flushes. Don't log in hot paths, since each write
  costs 2.5M points.
- **Any map.** Tournament maps are unseen, so nothing may depend on recognising a specific map. Map-specific
  tricks are allowed only as an optional extra that switches itself off when its predictions don't match
  what the dragon sees.
- **Prefer deleting code.** Remove what doesn't earn its keep, and leave the code cleaner than you found it.
- **Comments** say how a function is used, above its definition. Don't annotate every line.
