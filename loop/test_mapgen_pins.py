"""Pins every map style's maps: a style's output for existing seeds must never change silently, since the referee's test
maps (`eval`: near, community, trap, deadend, fortress, macro, curves and mix maps; worldgen and the portal bets beside it),
the shared map sets and every result recorded on them depend on it. Only an intended change updates PINS, saying why. mapgen/test_gen.py pins only normal,
odd and mix on seeds 0-11.

loop/mapideas.py runs this against a map-ideas branch's files before announcing it (MAPGEN_PINS_ROOT=<a folder with
the branch's mapgen/ and maps/>, inside a sandbox), so a new variation can't change the old styles' maps.

    cd loop && ../.venv/bin/python -m unittest test_mapgen_pins
"""

import testhome  # before anything that reads live paths
import hashlib
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.environ.get("MAPGEN_PINS_ROOT") or str(Path(__file__).resolve().parent.parent))
from mapgen.gen import generate  # noqa: E402

SEEDS = range(60)
# Styles pinned over fewer seeds: a worldgen map takes about 1.5 s (its generator judges 8 candidate worlds a map).
SEEDS_OF = {"worldgen": range(6)}
# sha256 over generate(seed, style) for SEEDS (or SEEDS_OF's), recorded 2026-09-26 at 2a4a0b7.
PINS = {
    # Changed on purpose 2026-09-27: half of normal and odd maps (so of mix) got a walled border, and nine in ten fast
    # farms (4 of 33 mix maps had a walled border and 14 of 33 no fast tile, against 7 and 2 of the 13 organiser maps).
    "normal": "4de7c834bd1048b06d71bfab3090ea342acc31cc0f7e0b619653690162b508f5",
    "odd": "bc6df9b6e368d75524bdeca750179fd84c327f5cb2de7653d30363e40dd7162a",
    "mix": "5a8f2ddd02dace3650fedc482a085a0966a59c482451c8a910aae36ce9c9723b",
    # Changed on purpose 2026-09-27: the ladder dropped Big Empty, Default Small and Stronghold (kept as near sources in
    # maps/retired) and added Portals and Slithery Fight, so near and eval cycle through 13 sources instead of 11.
    # And again 2026-09-27: half the near maps (mapgen.repearled) keep their source's layout but redraw its pearl field,
    # the organisers' variants that look the same with completely different pearl timings; the rest are unchanged.
    # Which half is a hash of the seed, so it doesn't follow the opponent seat (31 of these 60 seeds repearled).
    # Changed on purpose 2026-09-30 (I1099): a near copy keeps its source's sealed start rooms (gen.keeps_rooms), so
    # the copies of Portals, Stronghold and Trauma that opened one (12 of these 60) changed; every other map as before.
    # The same holds for eval, small, big, maze and nearportal below.
    # Changed on purpose 2026-09-30 again (E0405, I1079): a near copy keeps its source's portal pockets with their laps
    # and pearls, and teams that never meet (gen.kept_parts, gen.keeps_rooms), so the 28 of these 60 copies of Default,
    # Portals, Queen Of Spades, Slithery Fight, Autarky and Dilemma (the sources with pockets) changed; the rest as
    # before. The same for eval (14 of 60), sprint (all), small (33), big (10), maze (all) and nearportal (its 20
    # Portals copies) below.
    "near": "ca42dc64fa10f457440165532fa647e0d406a887b4d5db08ccf3e6f46c23962a",
    "trap": "549e2240cd51b4bb2c3b455c15aa08463abab0873b8806afd6f34570b17980cd",
    # Changed on purpose 2026-09-26: a ninth of eval seeds (seed % 9 == 1) became community-map variants, and another
    # ninth (seed % 9 == 5) trap maps; and 2026-09-27: another ninth (seed % 9 == 7, taken from mix) deadend maps,
    # another (seed % 9 == 8, also from mix) fortress maps, and the mix seeds changed with mix; then another ninth
    # (seed % 9 == 4, from mix) macro maps, the organisers' Sprint start (one very long dragon a team); then half the
    # near seeds repearled, as near; then the community map a community seed varies came from a hash of the seed
    # (gen.community_pick), since by the place in the cycle each community map went to one opponent seat (MISSES 59);
    # then 2026-09-29 half the last mix ninth (seed % 18 == 2: seeds 2, 20, 38 and 56 here) became curves maps, the
    # other 56 seeds byte for byte as before; then 2026-09-30 the near and community copies that opened a sealed start
    # room (gen.keeps_rooms; seeds 1, 10, 12, 24, 37, 51 and 55 here).
    # Then again 2026-09-30 the near and community copies of sources with portal pockets (gen.kept_parts; seeds 1, 3,
    # 10, 12, 15, 21, 33, 36, 37, 42, 46, 51, 54 and 55 here).
    "eval": "05cb177b3806f04700dbf010e5c8da306431f35b70b3fc281513ad1ca2dcbe14",
    # Added 2026-09-26 at b2e95e0 (appended to STYLES, so no older style's seeds moved): near variants of maps/sprint.
    # Changed on purpose 2026-09-30 (gen.kept_parts): Autarky's and Dilemma's 9-tile portal pockets are kept.
    "sprint": "1495787bca6ea64e652e9fec72c63e386c0c8d7653d9e2461ea2722713025de2",
    # Added 2026-09-27, appended to STYLES: small and big (review M28), deadend (fast pearls in dead-end pockets) and
    # maze (variants of the community Portal Labyrinth).
    "small": "480fc26320c2e15a0a71790c4b9b54db161ffdc4e128a854c734c4c5d764c924",
    "big": "60eb8559481d3585c920eb4a0e284d5e79cdaf923e415640bec4034d83e46a7c",
    "deadend": "137253840d753aebf1583db7a3814cda32c60fda13486f85bc589118301143c1",
    "maze": "4e084e9aa69174f58593ca6a2cd1118f3ac2668597c36686b9412f2d901eb94f",
    # Added 2026-09-27, appended to STYLES: walled halves joined through a few gates or only portals.
    "fortress": "2d0191c36a0f7f74ff291d364c90c900324201535dcf70e50091da6ed1b61341",
    # Added 2026-09-27, appended to STYLES: portalrooms (fast pearls in rooms entered only by portal, a third sealed;
    # not in eval) and macro (one long starter a team, a third coiled; a ninth of eval).
    "portalrooms": "918b90492726cbeec10424445d81255a6abcddfd4aa4d189e1d059051d9ad419",
    "macro": "4f921bf7b2fe7b172f6c8d340f10089cde9386ddd962827847782f08c822b0ae",
    # Added 2026-09-28, appended to STYLES: an independent generator's maps (mapgen/worldgen, vendored unchanged).
    "worldgen": "c7eaa2d000484783f2cdbe590f9f5677a7d0cf37f7a902f4b3a4575712119b22",
    # Added 2026-09-29, appended to STYLES: the portal bets (death portals, a vault behind portals, both at once), a
    # standing share of every test's maps (meta.PORTAL_SHARE).
    "deathportal": "5885968587a336b6781cb9b20b2377a8d83a0cbedb2fcf7a8010709b6f6ddb97",
    # Changed on purpose 2026-09-29, the day they were added: a vault door's far end is never in one view with its
    # mouth (gen.vault_door), as the death cells already weren't, so no dragon sees the good portal's landing.
    "vault": "0da88e9634c052906379658750c94b0f1c8a3176bd111bc055e0da3b941cfd41",
    "portalbet": "04132397e8dd41c2b5a285b1118e66d82276e106cff47d43d7b127970b9188cd",
    # Added 2026-09-29, appended to STYLES: Trauma's sealed start rooms (each team's left only by portal), for declared
    # mixes only.
    "sealedstart": "083ba2d95c2a93b20493ad851ad6194b8e8baa9feaacaaf25d0642e9007e16eb",
    # Added 2026-09-29, appended to STYLES: near variants of the portal ladder maps only (Trauma, Portals, Trophy), a
    # standing share of every test's maps as sealedstart became too. Changed 2026-09-30 with near (gen.keeps_rooms),
    # and again that day: its Portals copies keep Portals' laps and apart teams (gen.kept_parts).
    "nearportal": "25b5077ab4e0dbc2d806e328804575c1260038b591c341ef81fc9e8f66acef2a",
    # Added 2026-09-29, appended to STYLES: diagonal and curved kelp walls (rings, cups, diagonals, funnels, taichis),
    # an eighteenth of eval.
    "curves": "3dde7821d72c0ec9ca053889732296a83bf1f3938d939a6f3569c7134e8176ab",
}


class Pins(unittest.TestCase):
    def test_every_existing_style_makes_the_same_maps_for_the_same_seeds(self) -> None:
        for style, digest in PINS.items():
            with self.subTest(style=style):
                h = hashlib.sha256()
                for seed in SEEDS_OF.get(style, SEEDS):
                    h.update(generate(seed, style).encode())
                self.assertEqual(h.hexdigest(), digest)


if __name__ == "__main__":
    unittest.main()
