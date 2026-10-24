# Conflict for {id}: rebase your candidate onto the new champion

While you coded **{idea_id}**, the referee promoted a new champion, `{champion}`. Your commits conflict with it,
so your candidate can't be tested until it is rebased. Its changes since your base `{base}`:

```
{log}
```

```
{stat}
```

Rebase onto it and keep both changes: the champion's behaviour and your idea's.

1. `git rebase {champion}`. For each conflicted file, work out what each side meant, combine them, then
   `git add` it and `git rebase --continue`. Where both change the same logic, keep the champion's change and
   apply your idea on top of it, and say how you combined them.
2. Run the bot tests (`"{gate}" .venv/bin/python "{bot_tests_runner}" --bot bot --tests tests/bot`) and fix
   what the combination broke. Add or change tests only where your change meets the champion's. Then run the quick
   check (`{quick}`): the combined code is new code, as in the merge check after a clean rebase.
3. Finish with the branch rebased onto `{champion}`, every change committed and a clean tree. Edit only
   `bot/` and `tests/bot/`.

If the two can't sensibly be combined (for example, the champion replaced the code your idea changes), run
`git rebase --abort` instead and say why: the idea then goes back to the queue to be recoded on the champion.

The referee then reruns the bot tests and CPU check and plays the games against the new champion, as before.
End with a short summary of what conflicted and how you resolved it.
