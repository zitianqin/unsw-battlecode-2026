# Merge check for {id}: your candidate is now on a newer champion

While you coded **{idea_id}**, the referee promoted a new champion, `{champion}`, and your commits were rebased onto
it without a textual conflict. That only means git found no overlapping lines, not that the two changes still work
together. Its changes since your base `{base}`:

```
{log}
```

```
{stat}
```

Check the combination before any games are played:

1. Read what the champion changed (`git diff {base} {champion} -- bot`) next to your own change
   (`git diff {champion} HEAD`). Look for interactions: code your change relies on that now behaves differently,
   a parameter or switch whose meaning changed, a new rule that fires in the same situation as yours (so one now
   overrides, doubles or undoes the other), or a case your change now misses.
2. If they interact badly, fix it in `bot/` and `tests/bot/` so both changes do what they meant, and say how you
   combined them. If they don't, change nothing.
3. Run the bot tests (`"{gate}" .venv/bin/python "{bot_tests_runner}" --bot bot --tests tests/bot`). If you
   changed code, also run the quick check (`{quick}`). Finish with every change committed and a clean tree.

The referee then reruns the bot tests and CPU check and plays the games against the new champion, as before.
End with a short summary: what the champion changed that touches your idea, and what you checked or changed.
