import testhome  # before anything that reads live paths
import random
import unittest

import chance


class Chance(unittest.TestCase):
    def test_the_cases_that_fooled_the_loop_read_as_luck(self) -> None:
        # 2026-09-26: the online alert fired on 3 worse / 1 better; the M33 draft trigger on 7 of 10.
        self.assertGreater(chance.sign_p(3, 1), chance.TRIGGER_P)
        self.assertGreater(chance.sign_p(7, 3), chance.TRIGGER_P)
        self.assertLess(chance.sign_p(13, 5), chance.TRIGGER_P)  # 7359 vs 7003's longest margin: fires
        self.assertEqual(chance.sign_p(0, 0), 1.0)

    def test_a_rate_interval_and_a_paired_z(self) -> None:
        low, high = chance.rate_ci(7, 40)
        self.assertTrue(0.07 < low < 0.1 and 0.3 < high < 0.34)
        self.assertEqual(chance.rate_ci(0, 0), (0.0, 1.0))
        self.assertAlmostEqual(chance.paired_z([1, 1, 1, 0]), 3.0, places=5)
        self.assertEqual(chance.paired_z([1]), 0.0)

    def test_two_rates_apart_by_luck(self) -> None:
        # Review M41: 23 of 59 round-500 losses on 1152+ tiles ended at the merge floor against 13 of 74 wins, p 0.005.
        self.assertAlmostEqual(chance.rates_p(23, 59, 13, 74), 0.005, places=3)
        self.assertGreater(chance.rates_p(13, 74, 23, 59), 0.99)  # one-sided: the other way round is no finding
        self.assertEqual(chance.rates_p(1, 0, 3, 4), 1.0)
        self.assertEqual(chance.rates_p(2, 2, 4, 4), 1.0)  # both all: nothing to tell apart
        # A handful of games: 2 of 12 against 0 of 25 is luck 10% of the time, not the 2% a normal approximation gives.
        self.assertAlmostEqual(chance.rates_p(2, 12, 0, 25), 0.099, places=3)

    def test_a_corrected_threshold_keeps_luck_to_the_trigger_over_every_look(self) -> None:
        self.assertAlmostEqual(chance.low_p(-chance.corrected_z(1)), chance.TRIGGER_P)
        self.assertAlmostEqual(18 * chance.low_p(-chance.corrected_z(18)), chance.TRIGGER_P)
        self.assertGreater(chance.corrected_z(18), chance.corrected_z(4))  # more looks, a higher bar
        self.assertEqual(chance.corrected_z(0), chance.corrected_z(1))

    def test_rates_matched_map_by_map_leave_a_map_mix_out(self) -> None:
        # Each team acts 10% on one map and 5% on the other; only how many chances each map gives differs.
        mix = [([(10, 100), (10, 100)], [(2, 20), (2, 20)]), ([(1, 20), (1, 20)], [(10, 200), (10, 200)])]
        ours, theirs, p = chance.strata_gap(mix)
        self.assertAlmostEqual(ours, theirs)
        self.assertGreater(p, 0.5)
        ours, theirs, p = chance.strata_gap([([(1, 40), (2, 60)] * 3, [(20, 50), (30, 70)] * 3)] * 2)
        self.assertLess(ours, theirs)
        self.assertLess(p, 0.001)  # at the floor: no shuffle of 12 games a map is as far apart
        self.assertEqual(chance.strata_gap([([(5, 10)], [(1, 10), (2, 10)])]), (0.0, 0.0, 1.0))  # one game: no read

    def test_no_true_gap_reads_as_luck_at_its_nominal_rate_with_two_games_a_map(self) -> None:
        # The behaviour gaps review's case: 10 maps, 2 games a team on each, per-game rates spread about each map's
        # base the same for both teams. The normal tail read 12% of these as p <= 0.05; shuffles read 7% (5.0% of
        # 2000 such).
        rng = random.Random(1)
        game = lambda base: (lambda n, r: (sum(rng.random() < r for _ in range(n)), n))(
            rng.randint(20, 120), min(0.95, base * rng.lognormvariate(0, 0.6)))
        samples = [[(lambda base: ([game(base), game(base)], [game(base), game(base)]))(rng.uniform(0.05, 0.5))
                    for _ in range(10)] for _ in range(400)]
        rate = sum(chance.strata_gap(s, permutations=199)[2] <= 0.05 for s in samples) / len(samples)
        self.assertTrue(0.025 <= rate <= 0.08, rate)


if __name__ == "__main__":
    unittest.main()
