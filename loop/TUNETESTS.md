# Test update for {id}: the tuner changed numbers some tests pin

**{idea_id}** is a tuning idea: SPSA moved these `bot/params.hpp` values, and the commit on this branch holds them:

{moves}

The bot scenario tests now fail:

```
{failures}
```

Tests are editable evidence, not a specification (CLAUDE.md, Bot tests), and the SPRT, not the suite, decides
whether the new values play better. A test that fails only because it pins the old value of a tuned number (say it
builds a four-segment dragon and expects a split, which held only while `childLength` was 2) should state the
behaviour in terms of `P` instead (build the smallest dragon the current values let split, and one segment shorter
as the boundary), or be deleted if all it checks is the constant.

1. Read each failing test and the code it exercises. Decide, per test, whether it pins a tuned number or shows a
   real defect the new values cause: a crash, illegal output, or a combination that switches a behaviour off
   entirely (for example a child length that leaves no legal parent, so nothing ever splits).
2. Rewrite or delete only the tests that pin a number, in `tests/bot/`. Don't change `bot/params.hpp` or any other
   bot code, and don't weaken a test that shows a real defect: leave it failing and explain why.
3. Run the bot tests (`"{gate}" .venv/bin/python "{bot_tests_runner}" --bot bot --tests tests/bot`), commit, and
   leave a clean tree.

End with one line per failing test: rewritten, deleted or left failing, and why.
