"""Checks the blunder finder on small games played by the real engine with scripted moves: its rules reproduce
the engine move for move (portals, kelp, sprints, splits, head-ons, pearls), each mistake it names is flagged, and
a close case that isn't a mistake is not.

    .venv/bin/python -m unittest tools.test_blunders
"""

from __future__ import annotations

import random
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import blunders  # noqa: E402
from mapgen.gen import generate  # noqa: E402

W = H = 10


def edge(x: int, y: int, side: str) -> int:
    """Map-file index of the edge on `side` of tile x,y (map-files.md: north edges on even rows, west on odd)."""
    if side == "S":
        return edge(x, (y + 1) % H, "N")
    if side == "E":
        return edge((x + 1) % W, y, "W")
    return (2 * y if side == "N" else 2 * y + 1) * (W + 1) + x


def board(dragons: list[tuple[int, list[tuple[int, int]]]], kelp: list[tuple[int, int, str]] = ()) -> str:
    lines = [f"MAP {W} {H}", "SYMMETRY xy", "TILE_COUNT 0", f"EDGE_COUNT {len(kelp)}"]
    lines += [f"EDGE {edge(x, y, s)} 1 -1" for x, y, s in kelp]
    lines.append(f"DRAGON_COUNT {len(dragons)}")
    for team, body in dragons:
        lines.append(f"DRAGON {team} {len(body)} " + " ".join(f"{x} {y}" for x, y in body))
    return "\n".join(lines) + "\n"


# Plays a game with the engine: `bot(round, dragon id, input block)` returns that turn's command lines.
def play(map_text: str, bot: Callable[[int, int, list[str]], str], out: Path) -> Path:
    from unswbc.engine import EngineModule

    def reply(dragon_id: int, block: bytes) -> bytes:
        lines = block.decode().splitlines()
        r = int(lines[0].split()[1]) if lines and lines[0].startswith("ROUND") else -1
        return (bot(r, dragon_id, lines) + "\nENDTURN\n").encode()

    engine = EngineModule()
    engine.run(map_text.encode(), reply, None, None, lambda line: None)
    out.write_bytes(engine.replay("a", "b"))
    return out


# Scripted bots: per dragon, one move per round from round 0, repeating; no script means it gives up at once.
# From round `end`, team B's dragons (`quit`) give up, which ends the game.
def scripted(moves: dict[int, str], end: int, quit: set[int]) -> Callable[[int, int, list[str]], str]:
    def bot(r: int, i: int, _lines: list[str]) -> str:
        script = moves.get(i, "")
        if not script or (r >= end and i in quit):
            return "SUICIDE"
        return f"MOVE {script[r % len(script)]}"
    return bot


CIRCLE = "SENW"  # a length-3 dragon at (8,1) (9,1) (9,2) goes round its 2x2 square


class BlunderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def judge(self, map_text: str, bot: Callable[[int, int, list[str]], str], name: str = "g") -> blunders.Report:
        path = play(map_text, bot, self.dir / f"{name}.replay")
        rep = blunders.analyse(path, "A", 2, 1.0)
        self.assertEqual(rep.agreed, rep.checked, rep.disagreements)
        return rep

    def flags(self, rep: blunders.Report, r: int, i: int) -> list[dict]:
        return [f for f in rep.flagged if f["round"] == r and f["id"] == i]

    # The simulated rules must reproduce the engine exactly on messy random games: portals, kelp, sprints,
    # splits (legal and not), head-ons and death pearls.
    def test_rules_reproduce_the_engine(self) -> None:
        rounds = sprints = splits = portal_maps = 0
        for seed in range(40):
            if rounds >= 150 and portal_maps >= 2:
                break
            text = generate(seed, "odd")
            portal_maps += any(l.startswith("EDGE ") and l.split()[2] == "2" for l in text.splitlines())
            rng = random.Random(seed)

            # Mostly straight on (so games last), sometimes a turn, a sprint or a split, legal or not.
            def bot(r: int, i: int, lines: list[str]) -> str:
                length = next((int(l.split()[1]) for l in lines if l.startswith("LENGTH")), 2)
                facing = next((l.split()[1] for l in lines if l.startswith("DIR")), "N")
                turns = [d for d in "NESW" if d != "NESW"[("NESW".index(facing) + 2) % 4]]
                roll = rng.random()
                if roll < 0.05:
                    return f"SPLIT {rng.randint(1, max(2, length - 1))}"
                if roll < 0.15:
                    return "MOVE " + "".join(rng.choice(turns) for _ in range(rng.randint(2, 3)))
                return "MOVE " + (rng.choice(turns) if roll < 0.35 else facing)

            path = play(text, bot, self.dir / f"random{seed}.replay")
            rep = blunders.analyse(path, "A", 2, 1.0)
            self.assertEqual(rep.agreed, rep.checked, (seed, rep.disagreements))
            game = blunders.load(path, "A")
            rounds += rep.checked
            sprints += sum(1 for a in game.actions.values() if a[0] == "move" and len(a[1]) > 1)
            splits += len(game.births)
        self.assertGreaterEqual(rounds, 150)
        self.assertGreaterEqual(portal_maps, 2)
        self.assertGreater(sprints, 20)
        self.assertGreater(splits, 5)

    # From 2026-09-27 21:00 AEST the site may strip pearl timings from replays: the map text's TILE gaps and possibly
    # the pearlCountdown events. Before, a short TILE line raised in parse and a replay without countdowns failed the
    # rules check on every round a pearl spawned. A real ladder game (testdata/trophy-155732.replay) rewritten so.
    def test_a_replay_without_pearl_timings_still_reproduces_and_is_labelled(self) -> None:
        import test_watch
        real = test_watch.REPLAY
        full = blunders.analyse(real, "A", 2, 1.0)
        self.assertGreater(full.countdowns, 0)
        self.assertFalse(any(line.startswith("WARNING") for line in blunders.summary_lines([full], 5, "A", 2)))
        for how, edit in test_watch.WITHHELD.items():
            with self.subTest(how):
                cut = test_watch.rewritten(real, self.dir / "cut.replay", edit, countdowns=False)
                rep = blunders.analyse(cut, "A", 2, 1.0)
                self.assertEqual(rep.countdowns, 0)
                self.assertEqual((rep.checked, rep.agreed), (full.checked, full.checked), rep.disagreements)
                self.assertTrue(all("no_countdowns" in f["labels"] for f in rep.flagged))
                lines = blunders.summary_lines([full, rep], 5, "A", 2)
                self.assertTrue(lines[0].startswith("WARNING: 1 of 2 games have no pearlCountdown events"), lines[0])
                self.assertIn("cut.replay", lines[0])

    def test_kelp_death_with_a_safe_move_is_flagged(self) -> None:
        # Our dragon 0 walks north, then turns east into kelp while north and west are open.
        m = board([(0, [(2, 5), (2, 6), (2, 7)]), (1, [(8, 1), (9, 1), (9, 2)]), (0, [(6, 8), (6, 9), (7, 9)])],
                  kelp=[(2, 4, "E")])
        rep = self.judge(m, scripted({0: "NE", 1: CIRCLE, 2: "N"}, 6, {1}))
        (flag,) = self.flags(rep, 1, 0)
        self.assertIn("died_with_safe_move", flag["labels"])
        self.assertEqual(flag["cause"], "death")
        self.assertIn("kelp", flag["text"])
        self.assertGreaterEqual(flag["regret"], 3 + blunders.DEATH)

    def test_death_with_every_move_fatal_is_not_flagged(self) -> None:
        # Kelp on three sides and its own neck on the fourth: whatever it does, it dies.
        m = board([(0, [(2, 4), (2, 5), (2, 6)]), (1, [(8, 1), (9, 1), (9, 2)]), (0, [(6, 8), (6, 9), (7, 9)])],
                  kelp=[(2, 4, "N"), (2, 4, "E"), (2, 4, "W")])
        rep = self.judge(m, scripted({0: "N", 1: CIRCLE, 2: "N"}, 6, {1}))
        self.assertEqual(self.flags(rep, 0, 0), [])

    # Enemy dragon 1 dies on its first turn, leaving a pearl at 5,5 beside our dragon's head at 4,5.
    PEARL_MAP = board([(0, [(3, 5), (2, 5), (1, 5)]), (1, [(5, 5), (5, 6)]), (1, [(8, 1), (9, 1), (9, 2)])])

    # It never eats afterwards, so the window withdraws nothing.
    def test_walking_away_from_an_adjacent_pearl_is_flagged(self) -> None:
        rep = self.judge(self.PEARL_MAP, scripted({0: "E" + "N" * 20, 1: "", 2: CIRCLE}, 8, {2}))
        (flag,) = self.flags(rep, 1, 0)
        self.assertEqual(flag["cause"], "pearls")
        self.assertIn("walked_from_visible_pearl", flag["labels"])
        self.assertEqual(rep.withdrawn, [])

    def test_eating_the_pearl_is_not_flagged(self) -> None:
        rep = self.judge(self.PEARL_MAP, scripted({0: "EE" + "N" * 20, 1: "", 2: CIRCLE}, 8, {2}))
        self.assertEqual(rep.flagged, [])

    # Round 1 goes N and round 3 eats the pearl (N, E, S): at the 2-turn horizon east was a pearl better, but the
    # dragon really ate it within the window, so the regret is withdrawn rather than flagged.
    def test_pearl_eaten_past_the_horizon_is_withdrawn(self) -> None:
        rep = self.judge(self.PEARL_MAP, scripted({0: "ENES" + "S" * 20, 1: "", 2: CIRCLE}, 8, {2}))
        self.assertEqual(self.flags(rep, 1, 0), [])
        self.assertEqual([(w["round"], w["id"], w["windowAte"], w["reason"]) for w in rep.withdrawn],
                         [(1, 0, 1, "ate it later")])
        horizon_only = blunders.analyse(Path(rep.file), "A", 2, 1.0, window=0)
        (flag,) = self.flags(horizon_only, 1, 0)
        self.assertEqual(flag["cause"], "pearls")

    # Round 1 walks away north for good; dragon 3 comes west along row 5 and eats the pearl on round 3. A teammate
    # eating it means the team got it anyway (withdrawn); an enemy eating it doesn't change the flag.
    def test_pearl_a_teammate_ate_is_withdrawn_but_not_one_an_enemy_ate(self) -> None:
        for team, quit in ((0, {2}), (1, {2, 3})):
            m = board([(0, [(3, 5), (2, 5), (1, 5)]), (1, [(5, 5), (5, 6)]), (1, [(8, 1), (9, 1), (9, 2)]),
                       (team, [(9, 5), (9, 4)])])
            rep = self.judge(m, scripted({0: "E" + "N" * 20, 1: "", 2: CIRCLE, 3: "W" * 20}, 8, quit), f"t{team}")
            mine = [w for w in rep.withdrawn if (w["round"], w["id"]) == (1, 0)]
            if team == 0:
                self.assertEqual(self.flags(rep, 1, 0), [])
                self.assertEqual([(w["reason"], w["windowAte"], w["teammateAte"]) for w in mine],
                                 [("a teammate ate it", 0, 1)])
            else:
                (flag,) = self.flags(rep, 1, 0)
                self.assertEqual(flag["cause"], "pearls")
                self.assertEqual(mine, [])

    # Boundary: the same detour, but the pearl is eaten on round 7, one past the window (rounds 1-6).
    def test_pearl_eaten_after_the_window_is_still_flagged(self) -> None:
        rep = self.judge(self.PEARL_MAP, scripted({0: "ENNEESSW" + "W" * 20, 1: "", 2: CIRCLE}, 9, {2}))
        (flag,) = self.flags(rep, 1, 0)
        self.assertEqual(flag["cause"], "pearls")
        self.assertEqual(flag["regret"], 1)
        self.assertNotIn("horizonRegret", flag)
        # Later detour moves (rounds 2 and 4) do have round 7 within their window.
        self.assertEqual([w["round"] for w in rep.withdrawn], [2, 4])

    # Regression (online game 186335, round 45): an alternative was credited with a pearl that only appeared
    # because another dragon died next round. Here teammate 0 gives up on round 1, dropping a pearl at 5,4 that
    # going east on round 0 would have reached; nobody could have known.
    def test_pearl_dropped_later_is_not_credited(self) -> None:
        m = board([(0, [(5, 5), (5, 6)]), (0, [(3, 4), (2, 4), (1, 4)]), (1, [(8, 8), (9, 8), (9, 9)])])
        bot = scripted({0: "N", 1: "S" * 20, 2: CIRCLE}, 8, {2})
        rep = self.judge(m, lambda r, i, lines: "SUICIDE" if (r, i) == (1, 0) else bot(r, i, lines))
        self.assertEqual(self.flags(rep, 0, 1), [])

    # Our dragon 0 moves into the head of enemy 1 on round 0: both die. Enemy 2 keeps team B alive.
    def ram(self, ours: list[tuple[int, int]], theirs: list[tuple[int, int]]) -> blunders.Report:
        m = board([(0, ours), (1, theirs), (1, [(8, 1), (9, 1), (9, 2)]), (0, [(6, 8), (6, 9), (7, 9)])])
        return self.judge(m, scripted({0: "E", 1: "S", 2: CIRCLE, 3: "N"}, 6, {2}))

    def test_ramming_a_shorter_enemy_is_flagged(self) -> None:
        rep = self.ram([(4, 5), (3, 5), (2, 5), (1, 5), (0, 5)], [(5, 5), (6, 5)])
        (flag,) = self.flags(rep, 0, 0)
        self.assertIn("rammed_shorter_or_equal", flag["labels"])
        self.assertIn("died_with_safe_move", flag["labels"])
        self.assertEqual(flag["regret"], 5 - 2)  # we lose 5 to take 2

    def test_ramming_a_longer_enemy_is_a_good_trade(self) -> None:
        rep = self.ram([(4, 5), (3, 5)], [(5, 5), (6, 5), (7, 5), (7, 6), (7, 7)])
        self.assertEqual(self.flags(rep, 0, 0), [])

    # Our length-3 dragon goes round a 2x2 square; enemy 1 dies on round 0 and leaves pearls at 4,1 and 4,3,
    # two steps off the square, that ours never takes.
    def test_circling_past_a_pearl_is_flagged_and_labelled(self) -> None:
        m = board([(0, [(1, 1), (2, 1), (2, 2)]), (1, [(4, 1), (4, 2), (4, 3)]), (1, [(8, 6), (9, 6), (9, 7)])])
        rep = self.judge(m, scripted({0: CIRCLE, 1: "", 2: CIRCLE}, 20, {2}))
        circling = [f for f in rep.flagged if "circling" in f["labels"]]
        self.assertTrue(circling)
        self.assertTrue(all(f["round"] >= 7 and f["cause"] == "pearls" for f in circling))

    def test_circling_with_nothing_around_is_not_flagged(self) -> None:
        m = board([(0, [(1, 1), (2, 1), (2, 2)]), (1, [(8, 6), (9, 6), (9, 7)])])
        rep = self.judge(m, scripted({0: CIRCLE, 1: CIRCLE}, 20, {1}))
        self.assertEqual(rep.flagged, [])


if __name__ == "__main__":
    unittest.main()
