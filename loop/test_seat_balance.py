"""Every map style's, source family's and size band's share of each SPRT seat stays near its share of the whole set
(MISSES 59). referee/sprt.py hands trial i to seat i % (1 + len(driver.ROTATION)) (the champion, then the opponents),
reading a shared set without a mix in file-name order (driver.shared_set_maps) and a map mix, a shared set's default
one too, in driver.mix_sources' order, so any map property
chosen by a map's place in a cycle can line up with the seats and give one opponent only one kind of map: the repearl
branch did it for repearled near maps (its own test is test_driver's test_each_seat_gets_both_kinds_of_near_map), and
eval's community variants did it too (each of the four community maps went to one seat only, 2026-09-27).

A bucket counts once it has MIN_MAPS maps; each seat's share of it must be within LOW-HIGH of the set's share (the
0.35-0.65 band the repearl test puts round a half), or within SDS standard deviations of it by luck (a binomial draw):
a property chosen by a hash of the seed (repearled, community_pick) spreads by luck, and a 67-map family gets about 17
maps a seat, give or take 3.5, while a property that follows the seat gives a seat all or none. It generates one real
2,400-map eval set (about 17 s on this Mac, most of this module's time).

An opponent mix (driver.trial_order) is checked against the no-mix order instead (mix_taken_unevenly): the entry's
share of each bucket, and each seat's given-up trials against its own no-mix trials, at MIX_SDS. The set itself tilts
some families toward a seat by luck (near_unsw 23 maps on one seat against 12.8 expected, z 3.2, on the 2026-09-28 set),
and checking the mixed order's seats directly measured that luck: 5 of TRIAL_ORDER_SEED 1-10 failed there.

    cd loop && ../.venv/bin/python -m unittest test_seat_balance
"""

import testhome  # before anything that reads live paths
import random
import subprocess
import sys
import tempfile
import unittest
from collections import Counter, defaultdict
from pathlib import Path
from unittest.mock import patch

import driver
import meta

sys.path.insert(0, str(driver.REPO))
from mapgen import gen  # noqa: E402

PERIOD = 1 + len(driver.ROTATION)  # the SPRT's seats
SEAT = driver.sprt_seat(PERIOD)
LOW, HIGH = 0.7, 1.3
SDS = 4
MIX_SDS = 4.5  # mix_taken_unevenly's: it tests every bucket and seat in each of 153 cells (51 shares, 3 prefixes)
MIX_ENTRY = "earlyfold"
MIN_MAPS = 60
COUNT = driver.MAX_PAIRS  # a shared set's size
START = 10 ** 8 - COUNT // 2  # the file names cross a digit count, so their order isn't the seeds' order
MIX_STYLES = tuple(meta.MAP_STYLES)  # every style a map mix may name
SHARES = (0.25, 0.4, 0.5)
COMMUNITY = {p.stem for p in gen.community_sources()}


# The buckets a map is counted in: its style (a near family's maps are "near", a community map's "community"), its
# source family for near and community variants (a repearled family apart, as driver.map_style reads it) and, when
# known, its size band.
def buckets(style: str, area: int | None) -> list[str]:
    found = [f"size {meta.area_band(area)}"] if area is not None else []
    if not style.startswith("near_"):
        return [f"style {style}", *found]
    kind = "community" if style.removeprefix("near_").removesuffix("_repearl") in COMMUNITY else "near"
    return [f"style {kind}", f"family {style}", *found]


# Whether `got` of a bucket's `n` maps is off its expected share `p` of them: outside both LOW-HIGH of it and `sds`
# binomial standard deviations (any at all when none were expected).
def off(got: int, n: int, p: float, sds: float = SDS) -> bool:
    expected = n * p
    if expected == 0:
        return got > 0
    return not LOW <= got / expected <= HIGH and abs(got - expected) > sds * (n * p * (1 - p)) ** 0.5


# The buckets of at least `min_maps` maps whose count on some seat is outside both LOW-HIGH of their share of all `maps`
# ((seat, buckets) each) and SDS standard deviations of a binomial draw, as "bucket: n maps, per seat {...}" lines.
def unbalanced(maps: list[tuple[int | str, list[str]]], min_maps: float = MIN_MAPS) -> list[str]:
    total, per, seats = Counter(), defaultdict(Counter), Counter()
    for seat, names in maps:
        seats[seat] += 1
        for name in names:
            total[name] += 1
            per[name][seat] += 1
    faults = []
    for name, n in sorted(total.items()):
        if n >= min_maps and any(off(per[name][seat], n, seats[seat] / len(maps)) for seat in seats):
            faults.append(f"{name}: {n} maps, per seat {dict(per[name])}")
    return faults


# What an opponent mix's `order` (trial_order) does to the no-mix order `base` over trials with bucket lists `labels`,
# as fault lines: a bucket of at least `min_maps` maps the entry takes more or less than its share of, or one a seat
# gives up more or less of than its share of its own no-mix trials of it, beyond MIX_SDS. Only what trial_order
# decides, so a bucket the set already tilts toward a seat counts against the mix only if the mix tilts it further.
def mix_taken_unevenly(order: list[str], base: list[str], labels: list[list[str]], entry: str,
                       min_maps: float = MIN_MAPS) -> list[str]:
    total, taken, per, took = Counter(), Counter(), defaultdict(Counter), defaultdict(Counter)
    seat_trials, seat_given = Counter(), Counter()
    for seat, name, names in zip(base, order, labels):
        mine = name == entry
        seat_trials[seat] += 1
        seat_given[seat] += mine
        for bucket in names:
            total[bucket] += 1
            per[bucket][seat] += 1
            taken[bucket] += mine
            took[bucket][seat] += mine
    share = sum(seat_given.values()) / len(order)
    faults = []
    for bucket, n in sorted(total.items()):
        if n < min_maps:
            continue
        if off(taken[bucket], n, share, MIX_SDS):
            faults.append(f"{entry} took {taken[bucket]} of {n} {bucket}")
        for seat, trials in seat_trials.items():
            if off(took[bucket][seat], per[bucket][seat], seat_given[seat] / trials, MIX_SDS):
                faults.append(f"{seat} gave up {took[bucket][seat]} of its {per[bucket][seat]} {bucket}")
    return faults


# The (share, prefix) cells of every opponent-mix share meta.MAP_SHARE allows, in steps of 0.005, at prefixes 300,
# 1,200 and COUNT (the SPRT stops early; a bucket counts from its share of MIN_MAPS), whose trial_order over trials
# with bucket lists `labels` mix_taken_unevenly faults, with its first three lines.
def mix_faults(labels: list[list[str]]) -> dict[tuple[float, int], list[str]]:
    base = driver.trial_order({"rotation": list(driver.ROTATION)}, COUNT)
    low, high = meta.MAP_SHARE
    faults = {}
    for step in range(round((high - low) / 0.005) + 1):
        share = round(low + step * 0.005, 3)
        order = driver.trial_order({"rotation": list(driver.ROTATION), "opponents": {MIX_ENTRY: share}}, COUNT)
        for prefix in (300, 1200, COUNT):
            if found := mix_taken_unevenly(order[:prefix], base[:prefix], labels[:prefix], MIX_ENTRY,
                                           MIN_MAPS * prefix / COUNT):
                faults[share, prefix] = found[:3]
    return faults


# The style map of `seed` a map mix of `style` holds, as generate(seed, style) names it: a near-type style's source
# family (its source list cycles with the seed) or the style itself, and the source's area (a variant keeps its
# source's size; None for a random board). style_map_matches_generate checks this against real maps.
def style_map(seed: int, style: str) -> tuple[str, int | None]:
    sources = {"near": gen.near_sources(), "sprint": sorted(gen.SPRINT.glob("*.map")), "maze": [gen.MAZE],
               "nearportal": list(gen.PORTAL_LADDER),
               "small": gen.sized_sources("small") if style == "small" else [],
               "big": gen.sized_sources("big") if style == "big" else []}.get(style)
    if not sources:
        return style, None
    path = sources[seed % len(sources)]
    suffix = "_repearl" if style == "near" and gen.repearled(seed) else ""
    return f"near_{path.stem}{suffix}", gen.map_area(path.read_text())


class SeatBalance(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.folder = tempfile.TemporaryDirectory()
        out = Path(cls.folder.name) / "eval"
        # The repo's normal path: mapgen's command line, as driver.generate_maps runs it in the referee checkout.
        subprocess.run([sys.executable, str(driver.REPO / "mapgen/gen.py"), "--seed", str(START), "--count",
                        str(COUNT), "--out", str(out), "--style", "eval"], check=True, capture_output=True)
        cls.paths = sorted(map(str, out.glob("*.map")))  # file-name order, as shared_set_maps reads them

    @classmethod
    def tearDownClass(cls) -> None:
        cls.folder.cleanup()

    def labels(self, path: str) -> list[str]:
        return buckets(driver.map_style(Path(path)), driver.map_area(Path(path)))

    # A set without a map mix, in file-name order, is read only by policies frozen before 2026-09-28 (every claim since
    # freezes a worldgen mix, meta.claim_mix, whose eval maps are shuffled: mixed_maps), whose rotation had four
    # opponents, so it is dealt over their five seats. Over today's six a near family's maps would go to two seats only
    # (near_great_divide: 25 and 38 of 63 on seats 0 and 3), since eval's styles repeat every 9 seeds (18 since
    # curves).
    def test_a_shared_eval_set_gives_every_seat_its_share_of_each_kind(self) -> None:
        self.assertEqual(len(self.paths), COUNT)
        self.assertNotEqual(self.paths, driver.seed_order(Path(self.paths[0]).parent))  # the digit count changes
        seat = driver.sprt_seat(5)
        maps = [(seat(i), self.labels(path)) for i, path in enumerate(self.paths)]
        self.assertGreaterEqual(len({name for _, names in maps for name in names if name.startswith("family")}), 17)
        self.assertEqual(unbalanced(maps), [])

    # The bucket lists of map mix `mix`'s COUNT maps in the order driver.mixed_maps gives: its eval maps are links to the
    # real set's files of the same seeds, and its style maps empty files read through style_map (seeds after the eval
    # ones).
    def mixed_labels(self, mix: dict) -> list[list[str]]:
        real = {Path(path).name: Path(path) for path in self.paths}

        def generate_maps(out: Path, seed: int, count: int, style: str = "eval") -> None:
            out.mkdir(parents=True, exist_ok=True)
            for n in range(seed, seed + count):
                name = f"gen_{style}_{n}.map"
                if style == "eval":
                    (out / name).symlink_to(real[name])
                else:
                    (out / name).touch()

        with tempfile.TemporaryDirectory() as folder, patch.object(driver, "generate_maps", generate_maps), \
                patch.object(driver, "referee_styles", return_value=gen.STYLES):
            return [self.labels(path) if "gen_eval_" in path
                    else buckets(*style_map(int(Path(path).stem.rsplit("_", 1)[1]), Path(path).parent.name))
                    for path in driver.mixed_maps(Path(folder), START, COUNT, mix, SEAT)]

    # A map mix of each style at each share as claimed (meta.claim_mix: worldgen's share of the rest beside it), the
    # default worldgen-only mix of every shared set since 2026-09-28 and a two-style one.
    def test_every_map_mix_gives_every_seat_its_share_of_each_kind(self) -> None:
        mixes = [meta.claim_mix({style: share}) for style in MIX_STYLES for share in SHARES]
        for mix in [meta.claim_mix(None), meta.claim_mix({"small": 0.25, "deadend": 0.25}), *mixes]:
            labels = self.mixed_labels(mix)
            self.assertEqual(unbalanced([(SEAT(i), names) for i, names in enumerate(labels)]), [], mix)

    # An opponent mix (driver.trial_order) takes its entry's trials from the shared set in file-name order, where eval's
    # styles repeat every 18 seeds (9 before curves): at a share near k/9 a pick rule that repeats lined up with them
    # (driver.mix_plan's rule at 1/3 gave the entry 80 of the first 100 near maps and no fortress, mix or trap map). Every share
    # meta.opponent_mix_ok allows, in steps of 0.005, at prefixes as the SPRT stops early, checked against the no-mix
    # order (mix_taken_unevenly), since the set's own tilts (near_unsw leans to one seat with no mix at all) are not
    # the mix's doing. It passes whatever TRIAL_ORDER_SEED is (seeds 1-20 and 58 checked, 2026-09-28) and catches
    # mix_plan's order. A shared set of the default map mix (driver.shared_set_maps) is read in mixed_maps' order.
    def test_every_opponent_mix_takes_every_kind_evenly(self) -> None:
        labels = [self.labels(path) for path in self.paths]
        self.assertEqual(mix_faults(labels), {})
        with patch.object(driver, "drawn_plan", lambda count, share, seat, rng: driver.mix_plan(count, share, seat)):
            # The old rule is caught (shares near k/9 with 1 + len(driver.ROTATION) seats: 0.33, 0.335 and 0.445 at
            # six seats, 0.335, 0.365 and 0.445 at seven), whatever the rotation's length.
            self.assertTrue({share for share, _ in mix_faults(labels)} & {0.33, 0.335, 0.365, 0.445})
        self.assertEqual(mix_faults(self.mixed_labels(driver.shared_mix())), {})

    # style_map is how the mix test reads its style maps without generating 20,000 of them: checked against generate
    # on a sample of real seeds of each style.
    def test_style_map_matches_generate(self) -> None:
        rng = random.Random(59)
        for style in MIX_STYLES:
            for seed in [rng.randrange(1 << 28) for _ in range(3)]:
                text = gen.generate(seed, style)
                name = next(line.split()[1] for line in text.splitlines() if line.startswith("MAP_NAME "))
                family, area = style_map(seed, style)
                real = name.rsplit("_", 1)[0] if name.startswith("near_") else name.split("-")[1]
                self.assertEqual(family, real, (style, seed))
                if area is not None:
                    self.assertEqual(area, gen.map_area(text), (style, seed))


if __name__ == "__main__":
    unittest.main()
