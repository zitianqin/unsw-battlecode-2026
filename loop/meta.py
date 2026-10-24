"""The meta session: META_EVERY_S after the last finished review, or sooner when the queue runs low, rewrite the
playbook and the idea backlog from what has happened.

Run from the main checkout:   uv run loop/meta.py
(The driver calls `run_meta()` itself before each claim and after each result, and it launches a review when one is
due; the upkeep tick applies a finished background review with `apply_finished()`. Either applies it in a fresh
process, `.venv/bin/python loop/meta.py apply <loop home> <review folder>`, so a long-lived process never judges it
with old code: `fresh_line`.)

The session works on copies in a scratch folder and is sandboxed to that folder, so it can't touch the referee,
the results log or the bot. Its output is only accepted if the backlog still parses, keeps every existing idea's
id and history, and the playbook and method notes stay short; a new idea that bans a behaviour by citing a result is
left out and alerted, and the rest applies.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import re
import shutil
import stat
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from contextlib import contextmanager, suppress
from pathlib import Path

import chance
import quota
from home import EXTERNAL_CODE, YIELD_NICE, loop_home, nice_increment
from official import report as official_report, side_text as official_side_text

STATE = loop_home()
RESULTS = STATE / "results.jsonl"
IDEAS = STATE / "ideas.jsonl"
PLAYBOOK = STATE / "playbook.md"
EVIDENCE = STATE / "evidence.md"
# sig, 2026-09-29: "the bigger one every 2 days ish" (the review; the replay audit is audit.AUDIT_EVERY_S). It was
# every 20 results or after any promotion, several a day.
META_EVERY_S = 48 * 3600
LOW_QUEUE = 3  # fewer todo ideas than this bring a review forward, an hour after the last finished
MAX_PLAYBOOK_LINES = 60
MAX_METHOD_LINES = 40  # method.md: open findings about the tests themselves
# An idea's optional "kind" picks its test: tune (SPSA, then the SPRT), speed and identical (same moves as the
# champion instead of an SPRT), noloss (the SPRT as a non-inferiority test) or formula (a tuned constant or hard cutoff
# replaced like for like by a formula over what the bot observes, tested as noloss is: our generated maps are where the
# constant was fitted, so they are where a formula has least chance to show a gain; sig, 2026-09-28) or targeted (a
# fix for a rare way we lose, kept when no worse and better on the per-game measure it names, target_ok; sig,
# 2026-09-28); no kind is an ordinary gain test.
KINDS = ("tune", "speed", "identical", "noloss", "formula", "targeted")
# The kinds whose SPRT is the non-inferiority test (NOLOSS_ELO0, NOLOSS_ELO1); the probability rule reads the chance
# of being no worse than elo0 (noloss_probability).
NONINFERIOR_KINDS = ("noloss", "formula", "targeted")
# A targeted idea's measure must improve at this one-sided sign-test p over its target batch's paired units
# (driver.target_test).
TARGET_P = 0.01
TARGET_READ_SHARE = 0.8  # a target batch reading fewer of its replays is an error, never a verdict (driver.target_batch)
# A non-inferiority idea's bounds in normalized Elo: H1 "no worse than the champion" at 0 against H0 at -15. An idea may
# only declare a narrower elo0 (between -15 and 0) and never another elo1, so a declared bound can't wave a loss through.
# -15/0 has the gain test's power: a neutral change is kept 81% of the time, one worth -10 18% (2400 pairs).
NOLOSS_ELO0, NOLOSS_ELO1 = -15, 0


def noloss_bounds_ok(elo0: float, elo1: float) -> bool:
    return elo1 == NOLOSS_ELO1 and NOLOSS_ELO0 <= elo0 < 0


# A targeted idea's `"target": {"measure": name, "direction": "down" | "up"}`: a measure referee/measures.py knows
# (MEASURES, one side's count of something a game) and which way is better. Only the idea declares it (a review, a
# post-mortem, a follow-on, or the orchestrator with driver.mark_idea), and it freezes at claim with the policy.
def target_ok(target: object) -> bool:
    known = measures_module()
    return (isinstance(target, dict) and set(target) == {"measure", "direction"}
            and target["measure"] in known.MEASURES and target["direction"] in known.DIRECTIONS)


# referee/measures.py, the registry of target measures.
def measures_module():
    if str(REPO / "referee") not in sys.path:
        sys.path.append(str(REPO / "referee"))  # appended: loop/'s modules keep their names
    import measures
    return measures


# The opponent-mix entries a prompt offers: "`name` (what it is)" for each of MIX_OPPONENTS.
def mix_opponent_list() -> str:
    return "; ".join(f"`{name}` ({about})" for name, about in MIX_OPPONENTS.items())


# The target measures a prompt offers: "name (what it counts)" for each of referee/measures.py's MEASURES.
def measure_list() -> str:
    return "; ".join(f"`{name}` ({m.about})" for name, m in measures_module().MEASURES.items())


# An idea aimed at one map style may declare its test's map mix, `"evaluation": {"maps": {"sprint": 0.5}}`: that share
# of its SPRT (and tune) maps are fresh maps of that mapgen style ("near": variants of the ladder and Sprint-like maps,
# half repearled; "sprint": of the Sprint-like maps only; "small" and "big": of the ladder and community maps of one
# size, review M28; "deadend": random boards with fast pearls in dead-end pockets; "fortress": walled halves joined
# through a few gaps or only portals; "macro": random boards starting one long dragon a team; "portalrooms": fast pearls
# in rooms entered only by portal; "maze": of the Portal Labyrinth only; "sealedstart": each team's start room left
# only by portal, as on Trauma; "nearportal": of Trauma, Portals and Trophy only; "curves": random boards with
# diagonal and curved kelp walls; "worldgen": an independent
# generator's maps, held out from everything our generator's choices fitted), or several of them, together at most
# MAP_SHARE[1], so at least half the test is general maps (driver.mixed_maps; review M5/M9: E0050 read near 0.55
# against mix 0.50 in one test). General maps are eval's, worldgen's and the portal styles' (GENERAL_STYLES): every
# experiment with an SPRT adds those at claim (claim_mix).
# Speed and identical ideas play no SPRT, so there's nothing to aim.
MAP_STYLES = {"near": "variants of the ladder and Sprint-like maps, half of them with the pearl field drawn afresh",
              "sprint": "variants of the Sprint-like maps Dilemma and Autarky only: each team starts with one very "
                        "long dragon near the other's, in or beside rich pearl fields",
              "small": "variants of the ladder and community maps of at most 1024 tiles (Default Small, Devil, "
                       "Trophy, Default, Queen of Spades ...), where games are often won by elimination",
              "big": "variants of the ladder and community maps of 1152 tiles or more (Big Empty, Schooltime, "
                     "Stronghold, Trauma ...), where games rarely end before round 500",
              "deadend": "random boards with several dead-end pockets (a door 1 tile wide or 2 side by side, 1-27 "
                         "tiles, mostly 1-6-tile corridors) holding fast or every-round pearl tiles at their far end, "
                         "as every ladder map from Stronghold on has (Stronghold, Trauma, Dilemma, Autarky, Slithery "
                         "Fight)",
              "fortress": "random boards cut into the two teams' walled halves, joined through only 1-5 gaps (some "
                          "1-wide lanes of fast tiles) or, a third of them, only through portals with each team "
                          "starting in a box it leaves only by portal; most tiles barren, fast tiles in lanes and "
                          "dead ends, 2-4 short dragons a team: the shape of our five worst ladder maps (Devil, "
                          "Stronghold, Trauma, Portals, Slithery Fight)",
              "macro": "random boards where each team starts one dragon of 10-25 (a third of them coiled round its "
                       "own head, boxed in until it splits or its tail clears, as on Slithery Fight) beside 1-6 of "
                       "2-7: the start the organisers confirmed for the Sprint maps (after Dilemma and Autarky), on "
                       "layouts other than theirs",
              "portalrooms": "random boards with fast pearls in rooms entered only by portal (a third with the "
                             "teams sealed apart), from Portals: where a portal idea's moves are, since eval maps put "
                             "a third of an idea's trials on maps whose portals lead nowhere new (E0054's post-mortem)",
              "maze": "variants of the community map Portal Labyrinth only: a 32x32 portal maze, 43% of edges kelp and "
                      "8 portal pairs, where unseen edges assumed open walk dragons into dead ends",
              "worldgen": "maps from an independent generator another competitor wrote (mapgen/worldgen): districts "
                          "and biomes round one map-scale landmark (citadel, labyrinth, palace, walled city, "
                          "serpent hall, great wall, lanes, effigies or a portal lattice) on the official maps' "
                          "shapes, 1-7 dragons a team, 15-76% of tiles spawning pearls: ground none of our "
                          "generator's choices made, for a behaviour or number fitted on our maps (E0204's ram gate "
                          "helped on generated big maps but not on small or official ones)",
              "deathportal": "ordinary random boards with 1-3 death portals a team: a portal whose far side is a "
                             "one-tile cell walled in kelp, which kills whatever goes in, its mouth by pearls or on "
                             "the way to the other team, the cell baited with pearls half the time",
              "vault": "random boards where each team has a walled vault of 7.5-15% of the board, fast pearls on "
                       "every tile, entered only through 1-2 portals (two maps in five with a death portal beside "
                       "one)",
              "portalbet": "random boards of 1,600+ tiles where each team has 9 death portals and 1 vault portal "
                           "and no other, their mouths alike from outside: which is which takes a scout, sonar or "
                           "memory",
              "sealedstart": "Trauma-shaped boards (2:1, 20-28 tall) where each team starts in its own full-height "
                             "walled room at its end of the board, 18-28% of the width with at most a tenth of the "
                             "pearls, left only through 1-2 portals in its inner wall into a maze or open middle "
                             "holding the rest and 1-3 fast farms, where the teams meet: Trauma's opening, which "
                             "near_trauma copies keep too (since 2026-09-30; on only 67 of 231 maps before, "
                             "E0293's judge)",
              "nearportal": "variants of the three ladder maps whose portals decide games only: Trauma, Portals and "
                            "Trophy (its cup portal)",
              "curves": "random boards with 45-degree and curved kelp walls: 1-4 rings (some round a fast farm, as "
                        "Trophy's cup), cups, diagonals and funnels narrowing to a point, or on half the "
                        "xy-mirrored boards of 16+ tiles a side a taichi (a disc cut by an S-shaped wall, each team in its own comma), as "
                        "6 of the 13 organiser maps outline their pictures in diagonal staircases"}
MAP_SHARE = (0.25, 0.5)
# The share of an SPRT's (and a tune's) maps that are fresh worldgen maps when its idea declares no map mix (sig,
# 2026-09-28: "I think we should use the world gen script to generate maps more than we use our own for training", and
# "why not just do it all in one?"); the rest are eval maps. A declared mix keeps its styles at their shares and splits
# the rest between worldgen and eval in this proportion (claim_mix). A comparison of fits on our maps, on worldgen and
# on a mix, tested on held-out official maps, sets it later. 0.4375 from 29 Sep (was 0.5), less nearportal's 0.03125,
# so eval keeps about 0.31 beside PORTAL_SHARE.
WORLDGEN_SHARE = 0.40625
WORLDGEN = "worldgen"  # an independent generator's style (mapgen/worldgen), general ground (GENERAL_STYLES)
# The share of the same maps that are portal styles (sig, 2026-09-29: the portal bets, death portals, a walled vault
# behind a portal and "one combined map"; then every Trauma and portal map style, so a regression there can't hide),
# each style's part of it in PORTAL_STYLES: portalbet a quarter, since its maps pose the choice itself (which portal is
# safe), deathportal and vault an eighth each, portalrooms and sealedstart a fifth each, maze a tenth; nearportal's
# eighth (near variants of Trauma, Portals and Trophy, 0.03125 of the maps) was added on top, from worldgen's share, so
# the parts sum to 1.125. Frozen at claim like worldgen's; a policy frozen before a share has none.
PORTAL_SHARE = 0.25
PORTAL_STYLES = {"portalbet": 0.25, "deathportal": 0.125, "vault": 0.125, "portalrooms": 0.2, "sealedstart": 0.2,
                 "maze": 0.1, "nearportal": 0.125}
# General ground every test plays (claim_mix). Each but worldgen may also be declared as an aim (aimed): its declared
# share replaces its general share. Worldgen can't be, since its general share is itself an aim's size.
GENERAL_STYLES = (WORLDGEN, *PORTAL_STYLES)
# The mapgen styles a report splits out by name (a "gen-<style>-..." map); every other generated map counts as "mix".
# driver.map_style (the SPRT's style scores) and tools/fit.py's style_of both read it, so a new style reaches both (29
# Sep: sealedstart was in one list only, and fit read its maps as mix).
SPLIT_STYLES = ("trap", "deadend", "fortress", "portalrooms", "macro", WORLDGEN, "deathportal", "vault", "portalbet",
                "sealedstart", "curves")


# Each general style's share of the maps that aren't aimed (the rest eval): worldgen WORLDGEN_SHARE, the portal
# styles their part of PORTAL_SHARE. Read at each call, so a test patching either share sees it.
def general_shares() -> dict[str, float]:
    return {WORLDGEN: WORLDGEN_SHARE, **{style: PORTAL_SHARE * part for style, part in PORTAL_STYLES.items()}}


# A map mix's aimed styles: every style but worldgen and the general ones at a general share (GENERAL_STYLES: worldgen
# since 2026-09-28, the portal styles since 2026-09-29), which is below MAP_SHARE[0] (PORTAL_SHARE x its part, 0.0625
# at most), where an aim's starts. {} for no mix. An aimed mix plays its style maps on a private set, and its other maps
# and its screen on general maps, the champion's shared set when it may (driver.aimed_general, driver.screen_maps).
def aimed(mix: dict | None) -> dict:
    return {style: share for style, share in (mix or {}).items()
            if style not in GENERAL_STYLES or style != WORLDGEN and share >= MAP_SHARE[0]}


# The map mix an experiment freezes at claim for its idea's declared mix `declared` (driver.Experiment): the declared
# styles at their shares and each other general style at its share (`shares`, general_shares unless given:
# driver.apply_coder_mix passes the ones frozen) of the rest, eval the rest after that ({"big": 0.5} gives
# {"big": 0.5, "worldgen": 0.203125, "portalbet": 0.03125, ...}, none {"worldgen": 0.40625, "portalbet": 0.0625,
# ...}). A declared general style replaces its general share ({"portalrooms": 0.5} is half portalrooms, no more). A mix
# naming worldgen is frozen already and comes back as it is, a share of 0 adds none (None stays None: eval maps only;
# map_mix_ok refuses a 0 share), and 9 decimals keep general_mix exact. Anything but a dict of numbers comes back
# unchanged, for map_mix_ok to refuse.
def claim_mix(declared: object, shares: dict[str, float] | None = None) -> object:
    shares = {style: share for style, share in (general_shares() if shares is None else shares).items() if share}
    if not shares:
        return declared
    declared = {} if declared is None else declared
    if not isinstance(declared, dict) or WORLDGEN in declared \
            or not all(type(v) in (int, float) and math.isfinite(v) for v in declared.values()):
        return declared
    rest = 1 - sum(declared.values())
    return {**declared, **{style: round(rest * share, 9) for style, share in shares.items() if style not in declared}}


# The general part of map mix `mix`: each general style's share of the maps that aren't aimed (a declared general style
# is aimed, so it has none), the rest eval; None (eval only) for no mix or one without a general style, as experiments
# frozen before 2026-09-28 have.
def general_mix(mix: dict | None) -> dict | None:
    aims = aimed(mix)
    general = {style: share for style, share in (mix or {}).items() if style in GENERAL_STYLES and style not in aims}
    if not general:
        return None
    rest = 1 - sum(aims.values())
    return {style: round(share / rest, 6) for style, share in general.items()}


# The folder name of `champion`'s shared map set under toolkit `epoch` for general mix `mix` (driver.shared_set). The
# mix is in the name, so a baseline played on eval maps is never read for a worldgen mix; a set without one keeps the
# name eval sets had before worldgen.
def shared_set_name(champion: str, epoch: str, mix: dict | None = None) -> str:
    return f"{champion[:12]}-{epoch}" + "".join(f"-{style}{share:g}" for style, share in sorted((mix or {}).items()))


# Whether `mix` is a map mix an experiment may play: known styles, each aimed one's share within MAP_SHARE and all of
# them together at most MAP_SHARE[1], so general maps (eval's and GENERAL_STYLES') are at least half. In a frozen mix
# (one naming worldgen, claim_mix) a general style at a general share is not an aim: anything above 0, with all shares
# together at most 1 (claim_mix gives shares outside MAP_SHARE); in a declared one every style is an aim, worldgen
# never. Speed and identical ideas play no SPRT.
def map_mix_ok(mix: object, kind: str | None = None) -> bool:
    if kind in ("speed", "identical") or not isinstance(mix, dict) or not mix:
        return False
    if not all(style in MAP_STYLES and type(share) in (int, float) and math.isfinite(share) and share > 0
               for style, share in mix.items()):
        return False
    if WORLDGEN not in mix and len(aimed(mix)) < len(mix):
        return False
    shares = aimed(mix).values()
    return (all(MAP_SHARE[0] <= share <= MAP_SHARE[1] for share in shares) and sum(shares) <= MAP_SHARE[1] + 1e-9
            and sum(mix.values()) <= 1 + 1e-9)


# An idea whose change acts on an opponent the rotation (driver.ROTATION) never is, such as a king hunt or a fold
# against a team that ends the game thin, or an opening against a team that crosses portals first, may declare an
# opponent mix, `"evaluation": {"opponents": {"earlyfold": 0.33}}`: that share (MAP_SHARE's bounds) of its SPRT trials
# play that referee/gauntlet.toml entry, interleaved with the rotation's trials (driver.trial_order), which get the
# rest. Only the entries here, each measured against the champion to play a style online opponents show and the
# rotation lacks. earlyfold and earlyking end thin as most online opponents do (method M58: in 111 unselected online
# round-limit losses the opponent ended on a median 5 dragons, 8 or fewer in 79). In the same 20-map measurement the
# rotation's champion copy ended its round-limit games on a median 18 (8 or fewer in 6 of 26) and reverser on 8 (13 of
# 22), and swarm and breeder never fold; folder (6, 13 of 17) was thinner than those but less so than these two, and
# loneking reached the round limit in 9 of 40, on 2 dragons with a longest of 6, unlike any online opponent. scout
# takes the ground behind portals first (method M46; its gauntlet comment has the measurement). tradedown and rammer
# ram by rules other than our own code's (method M62); tradedown also plays a rotation seat, which its mix adds to
# (online.recheck_mix gives any keep's re-check a mix bringing tradedown's total to online.RECHECK_SHARE when the online
# compare falls). harvester, crowder, sprinter and starterking (method M44, M41) each play one style of the online
# leaders the rotation lacks (eating fight drops, contesting teammates' pearls, pouncing by sprint, keeping a long
# starter whole), a code switch on v0053 whose gauntlet comment has the measurement. feederking, a rotation seat too,
# is the only entry whose king out-grows ours (method M98: earlyking's trails ours by r400), so king and fold ideas
# declare it. rusher is the one entry measured to hold the contested middle early, as online opponents do on Devil (I1010,
# method M46: every rotation opponent runs our own race), so opening and denial ideas declare it. consolidator is the
# one entry whose king is long by r396, as the online teams that beat us on Trauma and Portals (weak-maps S4;
# feederking folds from r350), so king-seed and early-king ideas declare it. A speed or identical
# idea plays no SPRT, and a tune's SPSA plays the rotation only, so neither may declare one.
MIX_OPPONENTS = {"earlyfold": "v0028 folding in place from round 400 down to 4 dragons: against the champion cdcfba3 "
                              "on 20 fresh eval maps, both sides, 18 of its 40 games reached the round limit, all 18 "
                              "ending on 8 or fewer dragons (median 4.5; its second-longest 26 and longest 35 to our "
                              "longest 41.5; the champion won 14 of 18)",
                 "earlyking": "v0027 folding into its king from round 280: in the same games 17 of 40 reached the round "
                              "limit, all 17 ending on 8 or fewer dragons (median 4; its second-longest 12 and longest "
                              "22 to our 47; the champion won 14 of 17)",
                 "scout": "E0184's candidate whose poor-home starters cross a nearby unknown portal before farming: "
                          "against the champion 8f57a4e on 20 fresh eval maps, both sides, its first crossing was "
                          "median r11 to our r40 (a starter crossed in 17 of 40 games to 5), and on 10 portalrooms "
                          "maps r5.5 to r39; it won 17 of 40 and 10 of 20",
                 "tradedown": "v0028 ramming heads one shorter than itself when it has a local majority (also in "
                              "driver.ROTATION; a mix gives it more trials): 16.1 head-ons a game against shorter "
                              "heads to v0028's 3.8 on 20 fresh eval maps, and on 2026-09-28 rammed us on 0.19-0.20 "
                              "of gate-safe tiles, as the online teams behind v0051's alert did (0.19-0.24), where "
                              "the other rotation opponents rammed 0.10-0.17 (method M62)",
                 "rammer": "the swarm (v0011, never folding) taking every one-step ram it can reach, even when "
                           "outnumbered or trading evenly (its gauntlet note: it walks to no head); E0239, kept on "
                           "H1 and worse online, screened z -1.75 against it and -2.36 against tradedown (method M62)",
                 "harvester": "v0053 eating fight drops hard (contests every drop pearl in view, waits at a fight's "
                              "edge, rams only when our other heads reach more of the drop): against v0053 on 24 fresh "
                              "eval maps, both sides, it ate 52.6% of the head-on drop pearls either side ate, more "
                              "on 19 of 24 maps (luck alone 0.13%), where ranks 1-2 eat 64-79% online and we 54%; it "
                              "won 29 of 48",
                 "crowder": "v0053 contesting pearls instead of yielding them (no yield to a teammate or a nearer "
                            "enemy, no teammate repel): on the turns with a teammate's pearl in view it closed on one "
                            "on 63.8% to v0053's 59.4%, more on 22 of 24 maps (luck alone 2e-5), as ranks 1-2 "
                            "contest 57% to our 42% online; it won 15 of 48",
                 "sprinter": "v0053 pouncing by sprint: an enemy head beyond direct ram reach is closed on by one "
                             "sprint to a tile beside it and rammed next turn (direct rams stop at 3 steps): 4.6 "
                             "surviving sprints of 4+ steps a game to v0053's 0, and 9 of its 73 victims 15+ long died "
                             "to one, as rank 3 kills 35% of its 15+ victims online; it won 21 of 48",
                 "starterking": "v0053 keeping a long starter (10+) and its 8+ heir as a king that never splits or "
                                "trades while the rest breed: on Slithery Fight copies its starter line was median "
                                "24.5/31/56 long at r100/r300/r499 (v0053's gone by r300), as online teams that keep "
                                "theirs whole took 7 of 18 long-start games from 8258; it won 14 of 36 long-start "
                                "games and 19 of 48 eval ones",
                 "feederking": "a5e4552 folding from r350 into one king fed only by dragons too short to split, as the "
                               "online lone kings' teams do (also in driver.ROTATION; a mix gives it more trials): "
                               "against a5e4552 on 100 fresh maps, both sides, it kept 0.84 of its mass r350-r396 "
                               "(online kings 0.88) and its longest at r499 beat the champion's in 53 of 117 "
                               "round-limit games, the one local entry that out-kings us (method M98; earlyking's "
                               "longest trails ours)",
                 "rusher": "E0352's candidate whose opening race on maps of <=1024 tiles passes up home pearls, so its "
                           "starters hold the contested middle as online opponents do on Devil: against the champion "
                           "a0f0b7e on 30 fresh near_devil maps, both sides, its r10-60 share of head-turns on contested "
                           "ground was median 0.19 to our 0.03 (higher in 44 of 60, p 4e-4; online Devil losses 0.255 "
                           "to 0.015), first there r16 to r20, and it won 43 of 60; on contested-centre worldgen maps it "
                           "plays like the champion, and over 1024 tiles its race doesn't run, so pair it with a "
                           "\"small\" map mix",
                 "consolidator": "the champion 4e7a7a7 seeding its king from r250 on every map and folding from r300 "
                                 "with short feeders, as the online teams that out-king us on Trauma and Portals do: "
                                 "against 4e7a7a7 on 40 fresh portalrooms, sealedstart, fortress and small maps, both "
                                 "sides, 30 of 80 games reached the round limit, where its r396 longest was 8+ above "
                                 "ours in 20 (p 0.049), median 34.5 to 10.5 (online losses 28 v 7 and 21.5 v 8.5), "
                                 "and it won 18 of 30; its teams stay near 58 dragons to r300 (online about 30)"}


def opponent_mix_ok(mix: object, kind: str | None = None) -> bool:
    if kind in ("speed", "identical", "tune") or not isinstance(mix, dict) or len(mix) != 1:
        return False
    (name, share), = mix.items()
    return (name in MIX_OPPONENTS and type(share) in (int, float) and math.isfinite(share)
            and MAP_SHARE[0] <= share <= MAP_SHARE[1])


# Who SPRT trial `i` of report `sprt` (referee/sprt.py's sprt.json) played: its "schedule" when an opponent mix set one,
# else the rotation in turn (trial i against rotation[i % len(rotation)]); "champion" for a report from before the
# rotation.
def trial_opponent(sprt: dict, i: int) -> str:
    if schedule := sprt.get("schedule"):
        return schedule[i]
    rotation = sprt.get("rotation") or ["champion"]
    return rotation[i % len(rotation)]


# Reviews audit one area of the champion each, in turn, so every part of the bot gets questioned.
AUDIT_AREAS = (
    "pearl targeting and exploration (bot/targets.hpp)",
    "move safety, dead ends and space (bot/safety.hpp)",
    "move scoring weights (bot/scoring.hpp)",
    "splitting and split-outs (bot/split.hpp)",
    "rams and head-on trades (bot/ram.hpp and trades in bot/safety.hpp)",
    "fast-tile farms and the endgame king (bot/farm.hpp, bot/feed.hpp)",
    "world state, memory and vision (bot/state.hpp) and per-turn CPU",
)

# And each takes one first-principles lens, in turn: questions no loss statistic raises, because what the bot never
# does leaves no gap in the numbers (loop/MISSES.md: unread sonar echoes, unshared intents, not exploiting opponents).
LENSES = (
    "rules and API inventory: list every rule in docs/unsw and everything the controller API exposes (bot/helper.hpp); "
    "for each, does the champion use it, and does it exploit it (timing, execution order, sonar edge cases, portals, "
    "sprints, splitting rules)? Queue ideas for the largest unused ones",
    # REFLECT 2026-09-30 09:12 F1 (MISSES 103): leaders' games were read as a fair sample of their play, though a team
    # chooses what it shows us.
    "opponent model: for each style we meet (study.md leaders, online opponents, the gauntlet), what it gives away, how "
    "the bot could recognise it during a game (its sonar format, numbers, folding, aggression) and how to punish it; "
    "and what does it hide from us, and how would we know (a weaker bot in unranked or even ranked games, "
    "loop/decoys.py)",
    "pre-mortem: we lose the Grand Final on unseen maps to a leader; write the three most likely reasons, check each "
    "against the evidence, and queue ideas for the ones that hold",
    "endgame arithmetic: what decides round 500 (longest dragon) and eliminations; model what length and numbers we "
    "need when, against folders and swarms, and compare with what the champion actually reaches",
    # What the bot never does leaves no statistic, and nothing listed the known techniques it lacks (MISSES 24).
    "technique inventory: list the techniques strong bots use in comparable games (Battlesnake, Tron light-cycles, "
    "Halite, past Battlecodes: local tree search over nearby heads, Voronoi or territory scoring, opponent modelling, "
    "endgame solving, role assignment, and any others the research names); for each say whether the champion uses it "
    "(file:line), which experiments measured it (ids and verdicts, from stats.md), or that it was never played "
    "(stats.md's Never played), and queue or raise the largest never-played one",
    # The loop checked its tests' coverage (opponents, maps) but never its method's structural assumptions, until sig
    # asked why two changes worth little alone but much together were invisible (MISSES 21).
    # REFLECT 2026-09-28 20:52 F1, F2, F21: sig had to ask three times that verdicts be judged, not thresholded, then
    # supply map banks and a faster engine as speed levers, then ask whether a discarded candidate's code is thrown away.
    "method assumptions: list each assumption the acceptance method makes: one change at a time, with additive "
    "effects; a flat result means no value; results transfer across champions; the rotation's opponents represent the "
    "field; generated maps represent tournament maps; a fixed +15 bound suits every change; a fixed gate decides every "
    "verdict (name one result a fixed gate got wrong on its own evidence); quick checks and bot tests "
    "don't reject good changes; a verdict ends an idea's life (kept, promising, no-code and retest results all leave "
    "follow-on work); a discarded candidate's code is worthless; the engine and map making are fixed costs (take the "
    "largest non-game stage in throughput's stage times and queue a remedy for it). For each, name a good change the "
    "method would reject, then search stats.md (every "
    "result) and runs/E####/experiment.json for a case. Fix the method (queue the idea, or say in method.md what to "
    "change in the referee or loop), or record the assumption in method.md with the evidence that it holds",
    # REFLECT 2026-09-28 20:52 F3: the unknown-portal penalty (scoring.hpp) went unquestioned until sig lost on Trauma
    # (26-34 online, first crossing at median round 101 against the opponent's 12): lenses checked only new ideas.
    "champion hazards: pick ONE avoidance penalty or refusal already in bot/ (a penalty on a move, a guard that refuses "
    "a ram, a split or a crossing) that no waiting idea revisits; measure what it gives up (the rounds and pearls it "
    "costs, e.g. the round of our first portal crossing against the opponent's) split by map from our online per-map "
    "records (evidence.md's per_map, online.md) and the rule counts, and queue a measure-first idea whose Options: line "
    "weighs keeping it, a conditional version and its upside (GENERALISE.md: Every hazard has an upside)",
    # REFLECT 2026-09-28 20:52 F9: sig noted Cutlery's rise to rank 1 before any review studied what it changed.
    "leader deltas: take the team whose rating rose most in the last day (study.md, the leaderboard's history) and "
    "rank 1 if it changed, with the riser study's findings on it where you have them (loop/risers.py); compare its "
    "games before and after the rise (what it does more or less: splits, rams, "
    "folds, portal crossings, sonar) and say which of our waiting ideas matches each change, by id; raise the match's "
    "priority, or queue a measure-first idea for a change none matches",
    # Every tune keeps a threshold a threshold, so no review asked whether a fixed cutoff should be one at all, until
    # sig, 2026-09-27: "magic numbers everywehre for hardcutoff things eg when to fold etc etc - we should use
    # models or formulae or graphs or probailities or ratios or rl inference instead with more input values eg size or
    # other stuff maybe even rl too?", then "my formula idea wasnt just for folding - could apply to anythign and
    # eveyrhtign we have all the hard cutoffs we have too".
    "hard cutoffs: pick ONE hard cutoff in the champion (a round, area, count, length or distance threshold in "
    "bot/params.hpp or a constant in the code, e.g. trappedTailSpace, otherTailMargin, escapeMinLength, "
    "exploreRetarget, gossipMaxAge) that no idea in ideas.jsonl already replaces; measure the outcome it gates "
    "against the variable it stands in for (map size, distances, both teams' counts and lengths, the opponent's behaviour) with the rule "
    "counts and stats.md's map style and size splits, and say where the fixed value is wrong; then queue a "
    "measure-first idea replacing it with a formula, fitted curve, probability or learned gate over what the dragon "
    "observes, with constants that mean something (a ratio, a rate, a margin in segments) and a stop line saying how "
    "it rules out luck, tested on the decisions the formula changes and on layouts beyond our generator's (the idea "
    "rule, MISSES 61); for a decision taken often, a small gate fitted on our own games, as I452 proposes for rams",
    # REFLECT 2026-09-29 17:32 F7: the ram gate had been a learned model since E0204, and no other decision had one or
    # had one queued until sig asked four times ("we should do elarned models for the others right"): the hard-cutoffs
    # lens takes one cutoff a review, and nothing listed the decisions that lack a learned or formula gate.
    "decision inventory: list every decision the champion makes (the move, its target, sprinting, splitting, "
    "split-outs and sheds, rams and yields, portal crossings, folding, farm entries, what sonar it sends and trusts, and "
    "any other) with its file:line, how it is made (a constant or hard cutoff, a formula over what the dragon observes, "
    "or a learned gate such as bot/rammodel.hpp's) and its inputs, and how often it is taken from rule-counts.md; for "
    "each frequent one made by a constant or cutoff, or by a formula or gate missing inputs that matter (rounds left "
    "with length among them, GENERALISE.md), queue a measure-first idea for a learned or formula gate over every "
    "relevant input, name the waiting idea that covers it by id, or say why none would do better",
    # REFLECT 2026-09-30 03:11 F12: sig asked whether a dragon should plan around its teammates (match 609740) when the
    # data already said so (E0373's post-mortem: 589 doomed dragons shut in by teammates' bodies; E0372: Trauma deaths
    # from teammates jamming strips) and no lens asked it.
    "teammates as agents: for each death or trap, was a teammate's body, target or move involved? (E0373 found 589 "
    "doomed dragons shut in by teammates' bodies.) Measure that share in the rule counts and replays, and queue ideas "
    "where our dragons' plans collide",
    # REFLECT 2026-09-30 15:13 F6: sig's point that we "shouldn't give up too much ground" in the middle came as a
    # correction; no lens asked what ground the opponent takes first or what contesting it costs.
    "opponent's plan: for each style we meet, what ground does it commit to first (the middle, a pearl field, a portal "
    "mouth, our start), with how many dragons and by which round; what would contesting it cost us (dragons, rounds, "
    "the pearls we give up elsewhere) against what yielding it costs; measure both in replays and queue a "
    "measure-first idea whose Options: line weighs contesting, yielding and a formula of both sides' numbers there",
    # REFLECT 2026-09-30 15:13 F9, F27: sig had to ask whether dragons share what they know of the map (portal pairs
    # came only then, as I1115); area spawn rates stayed local. The rules/API lens lists what the game exposes, not
    # what one dragon knows and its teammates don't.
    "information inventory: for each fact a dragon stores (bot/state.hpp's fields, the map's symmetry, portal pairs, "
    "countdowns, pearl spawn rates by area, and any other), say whether it is shared over our own sonar, what a teammate "
    "would do with it, and its cost in bits a message; queue a measure-first idea for the largest unshared one worth "
    "its bits, or name the waiting idea that shares it by id",
)


# The audit area and lens of the review after `reviews` earlier ones. Round k of the areas pairs area a with lens
# a + k, so every (area, lens) pair comes round whatever the two lists' lengths (the plain index paired each lens with
# one area for ever once both were 7 long, 2026-09-27).
def review_focus(reviews: int) -> tuple[str, str]:
    area, rounds = reviews % len(AUDIT_AREAS), reviews // len(AUDIT_AREAS)
    return AUDIT_AREAS[area], LENSES[(area + rounds) % len(LENSES)]


PROMPT = """You are the meta session of an automated loop that improves our bot for UNSW Battlecode 2026, a
Snake-like programming game. Lanes each try one idea from the backlog; a referee keeps a change only if it
wins a statistical test against the current champion. Your job is to make the next experiments more likely to
succeed. You edit only ideas.jsonl, playbook.md and method.md in this folder.

Read `briefing.md` first: the orchestrator's short list of what every piece of research should assume (the contest's
rules, which references to trust, rivals' tricks, what has worked), kept current by it; follow it over older text.
Read: `stats.md` (every experiment so far, with outcomes and acceptance by category), `evidence.md` (what our
current champion dies to and loses to against the screen's folder, grower, swarm and earlyking, with round-499 margins), `study.md` (how the ladder's top five teams play, most games for ranks
1-2, next to our own current games), `scout.md` (recent public top battles), `discord.md` (the Discord
digests so far, newest last, then the latest read's messages: hypotheses only, and possibly out of date, since teams mislead on purpose and most rank below us; weigh a claim by the
poster's rank on the ladder, check it against what that team's games show in study.md, and only queue it as a tested
idea with the claim and its source in the hypothesis),
`online.md` (version-bound ranked/unranked per-map diagnostics of our ladder and autoscrim games, dev games apart:
per version our record against each dev team, the
organisers' bots, on one dev submission with its 95% interval and a map-matched compare with the previous version
(its sides line counts our dev games by side; a map row from one side's games mixes the map with that side: read
it beside the row's `local_<side>`, our local score on that map as that side);
and per version the round-500 losses: our dragons left, longest and total against their longest, the fold-all bound
and how many ended at the merge floor, by map size, and each side's king at r396 with its meals and split-offs after,
for losses and wins apart; and per map how our deaths and the opponent's came about, the doom table, MISSES 64;
then where our losses turned, by map and opponent: bceval's turning points, whose caveat it gives),
`playbook.md` and `ideas.jsonl`.
Do not combine evidence from different engine versions, bot submissions, opponents or sides as if it were
one controlled test. Never read a rate or mean from a set chosen by its result: runs/E####/losses, an online index's
replays marked `"kept": "loss"` (kept because we lost) and any losses-first pick are for watching; our online rates
come from every game, an index's `results` and `games` together (online.all_games), as online.md's records do
(MISSES 55). Check the current official docs in the shared policy; a toolkit upgrade may change rules.
Read context.md: inspect the actual current champion implementation, repository research and official mirrored
docs at those paths. For each failed or promising idea, distinguish a weak strategy from a flawed implementation;
consider a better algorithm, a small correction, or conditions/maps where it should apply. Cite evidence and
source experiment ids. A discarded result rejects that implementation on that champion, not the idea. For each
discarded or promising strategy idea, check whether it bundled independent behaviours, which part the lane's
diagnostics blame, and whether later promotions changed what it depended on; propose decomposed or corrected
follow-ups rather than dropping the direction. Call a direction dead only after its simplest clean form has
failed in isolation on a recent champion, and say which experiment showed that. Never write a ban into an
idea ("do not add X; that lost in E0026"): the validator rejects it. Cite past results as evidence with their
conditions, and leave the coder free to include the part most likely to carry the win. Post-mortem
follow-ups (source "postmortem:E####") come from the coder that built the failed candidate, and follow-on ones
(source "followon:E####") from the coder of a kept one, on what builds on it; weigh them, and re-rank or improve
them like any other idea. A quick fix (category "quickfix", its `quick_fix` naming the result it fixes and its
lineage's count, capped at driver.QUICK_FIX_CAP) is that coder's own small fix, already built and queued at
priority 1; each is a fresh test at 5%, so a change worth nothing tried three times (the original and two
fixes) is kept by luck about 14% of the time.
Revisit old results: for each
discarded or promising idea tested 3 or more promotions ago (see stats.md), say whether what it depended on has
changed, and if so add a retest idea on the current champion that cites the old experiment. When a concrete
algorithm/rules uncertainty matters, look it up in research/ and docs/unsw/ (the official docs, mirrored); you have
no web access and must not try to browse, since you also read Discord text written by strangers. Do not blindly
reread the entire research library every cycle. Scout observations
are hypotheses, not controlled evidence of our current bot's strength. Tiny genuine gains (even 1%) count when
there is enough fresh evidence. Never accept >50% alone or freeze obsolete tests/implementation.
Study the leaders first: compare ranks 1-2 and the dev benchmark (the organisers' bot, its side of our benchmark
games, never faked) in study.md with our own numbers there (sonar use and what it hits,
shared values, head-ons started against longer or shorter enemies, splits and pearls by phase), open at least two
of their listed replays to see how a difference arises, and turn the largest gaps into ideas that cite them.
Its "Gaps unchanged" section at the top lists gaps that outlived our last two submissions with no idea at work on
them: address each. `behaviour-gaps.md` gives how often we take each action the engine offers (portal crossings,
sprints, splits, rams) per turn that offered it and what it paid, beside ranks 1-2 on the same maps (a gap whose
leader's action clearly pays less than ours is not flagged: copy no action that cost its taker): each flagged gap gets
an idea (its title naming the action, its text behaviour gaps, so the report lists it) or, in your final lines, a stated
reason it needs none; a flag listing an idea at work needs only that id (REFLECT 2026-09-29 F3: our portal aversion
went unseen).
First-principles lens for this review: {lens}. Report what it found in your final lines, then add at least one
idea from it or say why none holds. Also name, in one line, the blind spot you think sig is most likely to catch
next, and act on it now if the evidence supports it.
Audit the champion's code in this review's area: {audit_area}. Accepted code is not correct code. Read that code,
then measure it in real games: how often each rule fires, whether it misfires, and whether it is simply a weak way
to play compared with the leaders in study.md. `rule-counts.md` has the counts from about 24 fresh games of the
current champion's logging copy played for this review (you can't play games yourself: maps drawn by style, {styles}
(rulecounts.STYLES), and across them by size, {sizes} (rulecounts.SIZES), each once against the champion, the swarm
and the breeder, each mean with its median over maps beside it and a column per size; a mean far from its median comes from a few maps;
deaths the debug line shows were on purpose, feeds and split-out parents that no enemy head killed, are counted apart as
`deliberate death`, not as young deaths or failures), fixed tallies over all its games (each ram by victim gain
from our length before the move, by steps and, once the debug line has it, head edge, with our share of the drops,
the net a ram and the swing, which counts what the victim lost too; each action's failures over its turns, with rams
and deliberate deaths apart, and a portal crossing's deaths by what they hit (teammate's head or body,
enemy head or body, itself, kelp); split children by their parent's logged rule on the split turn (split,
splitout, shed, threatshed or rule unknown) with their first-3-round failures, repeat split-outs within 5 rounds,
feeds and merges into the king; each folding dragon merged (fed), split out (a split-out's parent, deliberate), killed,
crashed on its own move or alive; crashes
from foldRound on by path steps to the king; farm entries by length and
phase with what followed; body < len and split-outs by the dragon's age and birth length; the debug line's flags as shares of logged turns), and a few of those replays to watch: losses first, which overstate
failure, plus at least one of each size and a blind pick per opponent, each on a different size and side whatever
its result; judge how common something is from the
tallies and the blind picks. If it says it is still being measured, read the rest first and come back to it. It shows which branch decided each turn, targets, space, score bands, splits and scan's
moments, and, from all its games, what decides round 500: each team's longest at r300, foldRound's start and r499,
its king's length at foldRound's start (the king line: the longest at r499 and the dragons it split from) and what
its own splits and sprint steps cost it, the growth from foldRound's start by source (a lead of another dragon over
the king at the start, or a split handing it on to a shorter child, is a change of king, not a loss of the king's;
the old head it sheds at a split-out is its own), deaths of our longest by phase and cause, and
how often our debug line's body < len. For a rule it can't see, watch
the kept replays (tools/watch.py dragon, frames) and also queue an "identical" idea that adds the rule's name to
the bot's debugIndicator log line (`"rule", name`; that line is off in real games, so they are unchanged), or, when
a log-field idea is already waiting, say "same change as" it (Identical ideas, below): later
reviews' rule-counts.md then tallies it. Add at least one idea that fixes, replaces or deletes what you find, citing
the measurement.
Think across the whole range, from game plan to single-tile details. Many small tweaks landing within
about +/-15 Elo means the bot may need a bigger change, not more tweaks. Ask what the strongest teams do
that we structurally can't: team-wide coordination and shared state over sonar, a decision structure with
named situations and triggers, phase plans (opening, midgame, round-500 endgame), deliberate ramming,
blocking and room control. Big ideas are welcome when grounded in evidence; pair structure with the behaviour
it enables (a pure refactor cannot pass the referee). Keep the default bound (+15) even for big changes; a
higher bound rejects real +10 to +20 gains. Predeclare +10 for a fresh confirmation of a promising result.
Group the "What recent experiments were blocked by" lines in stats.md by cause: when two or more share one, make
the missing capability an idea near the front (or raise the existing one) and give the ideas it blocks "after".
Order by what ideas need. When an idea needs information or code another would provide (a strategy that needs
an estimate of the enemy's longest, a sonar behaviour that needs messages to arrive), give it "after": [those
ids]; it then waits until they are tested. Put prerequisites that several ideas need near the front.
Look for complements. The referee judges one change at a time, so value that exists only in combination is
invisible (misses.md 21): gates in series (E0097 relaxed one split gate, -3.0; E0101 turned three off, +19.3),
information and its use, a prerequisite and its strategy. Among flat, promising and discarded
results, find pairs that touch one decision and queue a joint test: the use idea with "after": [the prerequisite's idea
id]. When it is claimed, a prerequisite that wasn't kept but left a candidate is cherry-picked under it and the pair is
tested as one candidate ("stacked on" in stats.md); a prerequisite that left none is built by its coder behind its own
switch. For gates in series, queue one idea that changes them together, naming every param in its title.
Revisit and re-check. stats.md lists results not kept that no later result or idea has come back to ("Not
revisited"), with each result's chance of a real gain: for each, retest it on the current champion (rebuilt if
the code it touched has changed) or say why its verdict still holds, in a history note on its idea naming the
result (E####). What the loop never ran leaves no gap in any number, so stats.md also lists "Never played":
waiting ideas no result played games for, oldest first, and the categories none of whose ideas
ever played. For each of its oldest 5 ideas and each never-played category, either
raise its priority, drop it with the reason in its history, or say why it waits. Queue a switch-off re-check (the
champion with a kept behaviour's switches off, at priority 1, so code that now hurts is found and deleted) only on
measured evidence that it now hurts: a `SPLIT:` line pulling against it, an online drop matched to it, rule counts
showing it misfire; its hypothesis gives that evidence with n and its chance. Never as a routine pick or because a
change was kept long ago: each costs a full SPRT, and the four played by 29 Sep found none that hurt (sig,
2026-09-29). A fallen online margin queues its own. Re-check a kept formula idea
(`"kind": "formula"`) only when off-generator evidence (the
raw ladder, retired, Sprint, community and adversarial map files, or `.online/` games) shows it doing worse: it was
kept for tying on our generated maps, where its old constant was fitted. Its re-check is an ordinary gain test, the
old constant put back as the candidate, decided on generated maps like any other; its coder's off-generator numbers
are a report, not a gate.
Conditional, not constant. When a behaviour helps against one opponent or situation and hurts against another,
the answer is usually not a better constant but a rule that depends on what the bot can observe: their numbers,
their longest seen, how hard they fold, how aggressive their heads are, the round, the map. Look for these splits
in `SPLIT:` lines in stats.md (the SPRT's per-opponent scores, the screen, or its map styles and sizes pulling
opposite ways), in online results by opponent, and in leaders who play opposite styles, and in any parameter moved
one way then the other.
Name the observable that separates the cases and propose the conditional version. Example: folding late won
against swarms but lost round-500 tiebreaks to a version folding early (E0053: champion 0.46, swarm 0.58), and the
fold round then moved 300 -> 450 -> 415; what it wanted was to fold just enough to beat the opponent's longest.
{idea_rule}
Options pass: ideas queued before the Options line existed never weighed their remedy against others, so most
avoidance rules never met a way to gain from the same situation (MISSES 71: the pearl-room factory). Take up to
{options_pass} waiting todo ideas whose remedy only avoids something (titles about pockets first, then avoid, refuse,
never, don't, hold back), skipping any whose hypothesis has an Options line or whose history has a review's Options
note. Give each a history note "Options: (1) ... (2) ... (3) ...; chosen: (n), why": 3+ remedies from different
framings (avoid it; turn it into a gain; combine existing behaviours; the opponent's mirror), each with its rough
reach. When the chosen one isn't the idea's own, queue it as a new idea (one of the 3-8) or name the waiting idea that
covers it; otherwise say why the avoidance stands.
stats.md's "Split findings" section lists groups of waiting ideas that look like one finding split into several
(meta.sibling_clusters: one category and overlapping evidence, from one audit or strongly overlapping from any
source) and that don't name each other (the loop's "flagged at intake as ..." note doesn't count: it decides
nothing), with any waiting idea that names two or more of them (a stack waiting on them, or one that says how it
differs). For each group, merge only true duplicates (the same change, as below) and parts that do nothing alone
without another member (one candidate, each part behind its own switch);
one mechanism asked for under different map conditions (heading for the centre on Devil and on small maps) is the
same change: merge those into one idea whose condition is the observable that covers every case. Otherwise add a history note to each member naming the others, the general quantity it moves and how it differs, or
add a stacked general version: a new idea with "after" on the members, the conditional rule built on their
candidates once each is tested, their cases its evidence and scenarios. Never merge a member into an idea that waits
on it ("after"): its change would then never be tested alone. Say which you did for each group.
Read misses.md: the classes of blind spot caught before, by sig or by us. For each class, look for a new instance
in this cycle's evidence and turn it into an idea or a method.md finding; say which classes you checked.
Exploit the opponent, not only fix ourselves. Every weakness found in our own play has a mirror: an opponent that
does the same thing can be punished. For each style we meet (the leaders in study.md, online opponents, the
gauntlet), ask what it gives away and how we would punish it, and add at least one idea that exploits a measured
opponent weakness, conditioned on seeing it. Example: review M1 saw never-fold copies eliminate our folding champion
and concluded only that we fold too early; the mirror, hunting an opponent that folded early, came from sig (I138).
Question the tests too, not only the bot: a referee verdict is only as good as what it plays. Check whether
local results transferred: the `compare` block in online.md (the current version against the previous one on
the same opponent versions, named or inferred (M73), maps, starts and pool periods, dev games apart), each promotion's opponent screen and official-map line in
stats.md, and online losses of a version the referee accepted. Where a change won locally but lost online, or
something else suggests a blind spot, work out why the local test missed it. Past examples: E0048 (+30) beat
only our own last version, which also folds late, then lost online to swarms, so a screen of different styles
was added.

1. `playbook.md`: add a one-sentence lesson for any mistake or dead end that shows up in two or more
   experiments, and remove lines the results have made untrue. Keep it under {max_lines} lines. Don't restate
   the game rules unless lanes keep getting one wrong.
2. `ideas.jsonl` (one JSON object per line):
   - Keep every existing line, including its id, status and history (a merge below is the one status change).
   - Add 3 to 8 new ideas with status "todo" and ids continuing the sequence. Each must be a testable
     change grounded in evidence, not a near-duplicate of anything already tried. Small genuine gains count.
     Include at least one big-picture strategy or architecture idea and at least one precise tactical fix.
     Include the intended behavior and a boundary/counterexample for a focused bot scenario in the hypothesis,
     and the rule's `Options:` line of 3+ numbered remedies (not one written `same change as I###` naming a waiting
     idea: it merges into that idea); a new idea without it, with a bare threshold in its rule or banning a
     behaviour by citing a result is left out when the review is applied (meta.refusal).
     Correctness tests complement, but never replace, CPU checks and statistical game evidence. An idea that
     removes or holds back an existing behaviour first says what that behaviour produces downstream, measured in
     replays (E0117 stopped "wasteful" splits unmeasured; their children gave the king about 1.5 length a game);
     if unmeasured, it is measure-first.
     Give each new idea an expected fire rate (how many moves a game it would change, and where), from rule counts or
     replays; an idea that can't say is measure-first. Every count in an idea or a method note carries its n and how
     likely luck alone gives it (a sign test or interval, loop/chance.py); a trigger you propose fires only at p <= 0.1. Read stats.md's "Predictable failures": a check a post-mortem
     says would have caught a failure before coding
     becomes a step in the ideas you write (and a rule proposal in playbook.md when it recurs).
   - A "promising" result / "followup" idea is positive but unproven, not accepted. You may propose a new
     id combining independent patches, or a targeted confirmation on fresh maps. Cite source experiment ids
     and predeclare evaluation settings (elo0, elo1, max_pairs, fresh) before any new games; every
     candidate that passes also faces the opponent screen. Never
     relabel old results, lower an active test's gate, or add gains from selected estimates.
   - Merge true duplicates: when two "todo" ideas would make the same change (audits, post-mortems and reviews
     add ideas independently), set the weaker one's "status" to "merged" and add "merged_into" with the kept
     idea's id; the coder of the kept idea then gets the merged idea's text too. Only for the same change:
     ideas that change different things stay separate, since a bundle hides which part helped.
   - Re-rank the priority (1 = do next, 5 = someday) of "todo" ideas. Lanes claim the lowest number first, so
     rank by expected payoff: the plausible Elo gain if it works, times the chance it works given the
     evidence, discounted a little for implementation risk. A likely +100 strategy change outranks a sure +5
     tweak. Count the test's cost too, since games are the loop's bottleneck: the SPRT stops early on clear results,
     so a real +15 decides in about 250-900 pairs while a +2 runs the whole 2400 and usually ends only "promising"
     (stats.md has each result's pairs). Rank by expected gain per test hour: bold, attributable changes (a measured
     gap to the leaders in study.md, a new behaviour) above small tweaks of an existing number; speed ideas, which make
     every later test faster, and prerequisites that other waiting ideas cite come early. Use the whole 1-5 scale:
     within a priority, ideas are claimed by `"rank"` (a number, lower first; ideas without one after every ranked
     one), then oldest first, so give a todo idea a rank (or change or drop its rank) to order it within its
     priority. Ideas sig adds (source "sig") keep their priority unless new evidence clearly says otherwise. A
     priority-1 todo idea stays at 1 (sig's rule; the loop keeps it whatever you write, and refuses a review that
     removes a "pin"): order it among the others with "rank", or with "after".
   - Tuning ideas: set `"kind": "tune"` and `"tune": {{"params": [names from bot/params.hpp], "constraints": [...]}}` (optionally
     iterations, perturbations, pairs; by default 10 iterations per value, at least 40, each on 12 fresh maps and
     about 42 games with the rotation's opponents, so 6 values take about 2,500 games). SPSA then tunes those
     numbers instead of a coding session, and the SPRT judges the result. Group 3-8 related numbers that decide
     one behaviour (splitting, ramming, farming, safety weights); larger groups are noisier and slower. An order the
     values must keep goes in the tune's constraints, e.g. `"tune": {{"params": ["foldRound"], "constraints":
     ["beaconRound <= foldRound"]}}` (<=, <, >=, >; between two params.hpp numbers, tuned or not), which the tuner
     enforces; an order stated only in the text is not enforced.
   - Speed ideas: set `"kind": "speed"` for a change that makes the bot use less CPU while playing exactly the
     same moves (faster test games, and CPU headroom online). The referee checks identical decisions, move for move
     on fresh maps, and a lower CPU instead of an SPRT, so any behaviour change fails it; propose behaviour changes
     normally.
   - Identical ideas: set `"kind": "identical"` only when every normal game's decisions must be unchanged, e.g.
     stricter checks on received sonar that only matter under attack. The referee checks identical decisions, move
     for move on fresh maps (games against sonar senders may still differ through the sonar key), and a CPU p99 no
     higher, as for speed ideas but with no CPU gain needed. An idea that only adds debug-log fields
     (the debugIndicator line, off in real and screen games) is written "same change as I###" naming the waiting
     log-field idea, which merges it, not as an identical idea of its own: each paid a full opponent screen (M42:
     E0127 2,600 games in 33 minutes, E0134 3,000 in 24).
   - No-loss ideas: set `"kind": "noloss"` only for a change whose effect on real games is none by design but which
     changes local test games (e.g. ending an artefact where both test teams read each other's sonar), and only
     when the hypothesis says why real games are unaffected, and for a prerequisite whose text says no move reads it
     yet (plumbing for later ideas; a gain test stopped E0299's at H0): noloss when its games can differ (timing,
     sonar, CPU); identical when they can't. A noloss SPRT tests non-inferiority (elo0 -15, elo1 0; an idea may
     declare an elo0 between -15 and 0): kept when not worse, discarded otherwise; the screen still applies.
   - Formula ideas: set `"kind": "formula"` for an idea that only replaces a tuned constant or hard cutoff (a
     round, length, distance or count a decision switches on) with a formula over what the bot observes, like for
     like: the same decision, no new behaviour, the old value a special case or limit of the formula where sensible
     (`fold when rounds left < path to the king + its length` for `from round 397`). Our generated maps are where the
     constant was fitted, so the SPRT only has to show the formula is not worse (the noloss bounds); it changes
     behaviour, so it gets a follow-on when kept. Nothing gates like for like: check it in each formula result's
     diff. An idea that also adds or changes a behaviour stays ordinary.
   - Targeted ideas: set `"kind": "targeted"` and `"target": {{"measure": name, "direction": "down"}}` (or "up") for
     a fix of a rare way we lose, which can't show a gain on general maps (E0207, a pocket gate, ended level over
     1,590 pairs). Measures ({measures}) are one side's counts a game from a game's replay. It is kept when the
     SPRT shows no loss (the noloss bounds) and then, in a batch of fresh maps where the measure can fire (the
     candidate and the champion each against the rotation), the measure improves in that direction at sign-test p
     <= {target_p}, with every other gate. Pick the measure the change acts on, and
     say in the hypothesis what a fix that games it (fewer pocket deaths by never entering pockets) would cost.
   - Map mix: an idea aimed at one map style (e.g. Sprint openings, where each team starts with one very long dragon
     near the other's in rich pearl fields, with "sprint"; or the small walled maps' early economy, with "small")
     may declare `"evaluation": {{"maps": {{"sprint": 0.5}}}}` or `{{"small": 0.4}}`: that share (0.25-0.5) of its
     SPRT maps (and of a tune's maps) are fresh variants of the Sprint-like maps only ("sprint"), of the ladder and
     Sprint-like maps, half of them with the pearl field drawn afresh on the same layout ("near"), of the ladder and
     community maps of one size ("small", "big"), of the Portal Labyrinth only ("maze", a kelp-and-portal maze),
     random boards with fast pearls in small dead-end pockets behind 1- or 2-wide doors ("deadend", as the ladder
     maps from Stronghold on have), random boards cut into walled halves joined through a few gaps or only portals
     ("fortress", our five worst ladder maps' shape), random boards where each team starts one long dragon, some
     coiled round their own head ("macro"), the rest general maps, so a gain there isn't diluted by them (ordinary
     eval maps are about 5% Sprint-like and a ninth macro). Two aimed styles may share the 0.5 ({{"small": 0.25,
     "deadend": 0.25}}). General maps are the usual eval mix, an independent generator's ("worldgen", ground our
     generator never made) and the portal styles (meta.PORTAL_STYLES: portal bets, portal rooms, Trauma's sealed
     starts, the Portal Labyrinth and near variants of the portal ladder maps): with no mix declared,
     meta.WORLDGEN_SHARE of every SPRT's and tune's maps are worldgen and meta.PORTAL_SHARE portal styles, and a
     declared mix's rest is split among them in that proportion. A portal style may be aimed at like any other: its
     declared share replaces its general one; worldgen can't be. Map size decides the kind of game (submission 7003's 100 online games: no elimination on maps of
     1152+ tiles, all 14 elimination losses on 1024 or fewer), so an idea that acts on one size may aim at it:
     "small" (at most 1024 tiles) for opening, early-economy and elimination ideas, "big" (1152 or more) for fold,
     endgame and king ideas; every result's map-style line also gives its score by size (<=1024, 1025-2399, >=2400
     tiles). Its opponent screen still plays general maps. With an aimed style the SPRT's champion games can't be
     reused from the shared map set, so each trial costs about 3.5 games instead of 2: declare it only when the idea
     targets that style.
   - Opponent mix: an idea whose change acts on an opponent the SPRT's rotation never is may declare
     `"evaluation": {{"opponents": {{"earlyfold": 0.33}}}}`: that share (0.25-0.5) of its SPRT trials play that
     referee/gauntlet.toml entry, the rest the rotation as usual. Only {mix_opponents}. Its opponent screen is
     unchanged, and it may go with a map mix.
   - Fields: id, category, priority, title, hypothesis, status, source ("meta").
3. `method.md`: open findings about the tests themselves, for the orchestrator and sig: each with the evidence
   (versions, games, numbers), why it matters for the tournaments or the ladder, and a concrete change to the
   referee, opponent screen (referee/gauntlet.toml), map mix or gates. You can't change those; say what to
   change. Remove findings that were fixed or turned out wrong. Keep it under {max_method} lines; leave it
   unchanged when you find nothing new.
Finish with four lines: what you changed in the playbook, which ideas you added, and why, and a `Backlog:` line
(REFLECT 2026-09-29 17:32 F6: waiting ideas grew to about 370, duplicates and stale ones among them, and no report
said so): the todo ideas now against the last review's start (the newest `review <ns>` history note in ideas.jsonl,
a time in ns), from the ideas added and the ideas that stopped waiting since then (history notes dated after it,
statuses, stats.md's results), whether the count is shrinking, then the duplicates merged, stale ideas closed and
complements stacked since that review, yours and others', each by id, or "none".
"""


@contextmanager
def locked(path: Path):
    """The same lock the driver's lanes take on the backlog (<path>.lock)."""
    with open(path.with_suffix(path.suffix + ".lock"), "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


# Whether this caller is the only one running a background worker's whole step (run_meta, audit.tick and their
# apply_finished): yields True while it holds <path>.step.lock, False at once when another process or thread does, and
# the caller then does nothing, since someone else is launching or applying that worker. Several drivers, native
# upkeep and each driver's upkeep thread run these steps; one holder means a finished worker is applied once and never
# relaunched while it is being applied. A separate file from locked(path), which the step takes inside.
@contextmanager
def only_one(path: Path):
    with open(path.parent / f"{path.name}.step.lock", "w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True


# Appends `text` as a line to <state>/host-alerts.log, the file the orchestrator watches: a driver's own log is read
# only when it exits.
def alert(state: Path, text: str) -> None:
    with (state / "host-alerts.log").open("a") as handle:
        handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")


# " Screen: swarm 0.44 vs champion 0.60 (worse), folder 0.52 vs champion 0.50 (stopped at 25 maps). Official maps 47/90
# vs champion." for a result (a two-stage screen's stop and a single-entry hold's replay: driver.check_pool), or "".
# The official maps are played after the result is recorded (loop/official.py): its runs/<id>/official.json under
# `state`.
def screen_text(row: dict, state: Path = STATE) -> str:
    text = ""
    if opponents := row.get("pool", {}).get("opponents"):
        pairs = row["pool"].get("pairs_per_opponent") or 0
        text += " Screen: " + ", ".join(f"{o['name']} {o['candidate']:.2f} vs champion {o['base']:.2f}"
                                        + (" (worse)" if o.get("worse") else "")
                                        + (f" (held on it alone at z {o['z']:+.2f}, replayed on {r['maps']} fresh maps "
                                           f"{r['candidate']:.2f} vs {r['base']:.2f}, z {r['z']:+.2f}"
                                           + (f", pooled z {r['pooled']['z']:+.2f}" if r.get("pooled") else "")
                                           + f": {'held' if r['worse'] else 'passed'})"
                                           if (r := o.get("replay")) else "")
                                        + (f" (stopped at {o['stopped_at']} maps)" if o.get("stopped_at", pairs) < pairs
                                           else "") + longest_text(o.get("r500", {}))
                                        for o in opponents) + "."
        if family := row["pool"].get("family"):  # driver.family_row, method M62
            text += (f" Ram family ({', '.join(family['members'])}) pooled {family['candidate']:.2f} vs champion "
                     f"{family['base']:.2f}, z {family['z']:+.2f}" + (" (worse)" if family.get("worse") else "") + ".")
    elif skipped := row.get("pool", {}).get("skipped"):
        text += f" Screen: not played ({skipped})."
    rotation = {k: v for k, v in row.get("sprt", {}).get("per_opponent", {}).items()
                if k != "champion" and v.get("score") is not None}
    if rotation:
        text += " In the SPRT vs " + ", ".join(f"{k} {v['score']:.2f}" for k, v in rotation.items()) + \
            " (0.50 = level with the champion)."
    if "score" in (official := official_report(state, row)):
        text += f" Official maps {official['score'] * official['games']:g}/{official['games']} vs champion" + \
            official_side_text(official) + "."
    sprt = row.get("sprt", {})
    return text + mix_text(row) + style_text(sprt.get("per_style", {}), sprt.get("per_area", {})) + split_text(row)


# " Map mix: 50% sprint and 25% worldgen maps, the rest eval." for a result that played a map mix (map_mix_ok), with
# "set by its coder: <why>" for one its coder chose (driver.apply_coder_mix), then " Opponent mix: 33% earlyfold
# trials, the rest the rotation." for one that declared an opponent mix (opponent_mix_ok), else "".
def mix_text(row: dict) -> str:
    evaluation, text = row.get("evaluation") or {}, ""
    if mix := evaluation.get("maps"):
        shares = " and ".join(f"{100 * share:g}% {style}" for style, share in mix.items())  # 1.5625%, not 2%
        coder = f", set by its coder: {str(evaluation.get('maps_why'))[:160]}" \
            if evaluation.get("maps_source") == "coder" else ""
        text += f" Map mix: {shares} maps, the rest eval{coder}."
    if mix := evaluation.get("opponents"):
        (name, share), = mix.items()
        text += f" Opponent mix: {share:.0%} {name} trials, the rest the rotation."
    return text


# " Its coder skipped the review." for a result whose coder committed a candidate without starting the fresh-context
# reviewer loop/PROMPT.md asks for (driver.coder_review), else "".
def review_text(row: dict) -> str:
    skipped = (row.get("session") or {}).get("review") == "skipped" and row.get("changed_files")
    return " Its coder skipped the review." if skipped else ""


# " SPLIT: helps vs swarm 0.58, hurts vs champion 0.46 ..." when the result pulls opposite ways by opponent (the SPRT's
# per-opponent scores, at least 100 trials each, or screen z-scores of +/-2), then map_split_text's line and a tune's
# generalisation check (tunesplit.text of session.tune_split): usually a sign the behaviour should depend on something
# the bot can observe, not a constant (see the review prompt).
def split_text(row: dict) -> str:
    scores = {k: v["score"] for k, v in row.get("sprt", {}).get("per_opponent", {}).items()
              if v.get("score") is not None and v.get("trials", 0) >= 100}
    helps = {k: v for k, v in scores.items() if v >= 0.53}
    hurts = {k: v for k, v in scores.items() if v <= 0.47}
    # An entry replayed after a lone hold (driver.check_pool) reads by its pooled z, first games and replay together.
    screen = {o["name"]: (o.get("replay") or {}).get("pooled", o).get("z", 0)
              for o in row.get("pool", {}).get("opponents", [])}
    helps |= {f"{name} (screen)": z for name, z in screen.items() if z >= 2}
    hurts |= {f"{name} (screen)": z for name, z in screen.items() if z <= -2}
    import tunesplit  # here: tunesplit imports this module
    text = map_split_text(row) + tunesplit.text((row.get("session") or {}).get("tune_split") or {})
    if not (helps and hurts):
        return text
    show = lambda items: ", ".join(f"{k} {v:.2f}" if "(screen)" not in k else f"{k} z {v:+.1f}" for k, v in items.items())
    return (f" SPLIT: helps vs {show(helps)}, hurts vs {show(hurts)}: consider a version conditioned on what the bot "
            "can observe of the opponent." + text)


SPLIT_MIN_TRIALS = 8  # a map bucket with fewer trials is too coarse for a z-score
COMMUNITY_MAPS = Path(__file__).resolve().parent.parent / "maps" / "community"


# The near styles made from the Discord's community maps (mapgen.community_sources), e.g. near_great_divide: a report
# pools them apart from the ladder and Sprint maps' near variants.
def community_styles() -> set[str]:
    return {f"near_{p.stem}" for p in COMMUNITY_MAPS.glob("*.map")}


# `styles` (per_style) split into the ladder and Sprint maps' near families and the community maps' ones.
def near_groups(styles: dict) -> tuple[dict, dict]:
    community = community_styles()
    near = {k: v for k, v in styles.items() if k.startswith("near_")}
    return {k: v for k, v in near.items() if k not in community}, {k: v for k, v in near.items() if k in community}


REPEARL = "_repearl"  # mapgen's name for a near map with its pearl field drawn afresh: near_<map>_repearl_<seed>


# `styles` (per_style) with each repearled near family (near_<map>_repearl: the same layout with the pearl field drawn
# afresh, mapgen.repearled) pooled into its layout's (near_<map>), and the repearled families pooled on their own ({}
# without any). One bucket a layout keeps its trials as before half the near maps were repearled; the pearl
# kind shows as the one pooled family, which adds one look to the SPLIT bar.
def by_layout(styles: dict) -> tuple[dict, dict]:
    repearl = {k: v for k, v in styles.items() if k.startswith("near_") and k.endswith(REPEARL)}
    layouts: dict[str, dict] = {}
    for k, v in styles.items():
        layouts.setdefault(k.removesuffix(REPEARL) if k in repearl else k, {})[k] = v
    return ({k: pooled(g) if len(g) > 1 else next(iter(g.values())) for k, g in layouts.items()},
            pooled(repearl) if repearl else {})


# One {trials, score} for several buckets, weighted by trials.
def pooled(buckets: dict) -> dict:
    trials = sum(v["trials"] for v in buckets.values())
    return {"trials": trials, "score": sum(v["score"] * v["trials"] for v in buckets.values()) / trials}
TRIAL_SD = 0.156  # median per-trial score SD over 17 kept results' SPRTs (2026-09-27), for a result without per_map

# Map size bands in tiles (name, lowest, highest). Review M28: online, maps over 1024 tiles (mapgen's SMALL_MAX) rarely
# end in an elimination and smaller ones often do, and the 64-dragon cap binds only from 2400 tiles (Schooltime, Big
# Empty), so Stronghold and Trauma (1152) sit in the middle band. mapgen's `big` style (from BIG_MIN) spans the top two.
# The SPRT's per-size scores (driver.area_scores) and online.md's round-500 losses (online.round500) use them.
AREA_BANDS = (("<=1024", 0, 1024), ("1025-2399", 1025, 2399), (">=2400", 2400, float("inf")))


# The AREA_BANDS name of a map of `area` tiles.
def area_band(area: int) -> str:
    return next(name for name, low, high in AREA_BANDS if low <= area <= high)


# The SPRT's map buckets (each style, a near layout's plain and repearled maps as one (by_layout), near, repearled and
# community maps each pooled, each size band) with SPLIT_MIN_TRIALS or more trials, each with its z against level (0.50)
# from the result's own per-trial score SD (per_map, else TRIAL_SD), and the z a bucket must pass (chance.corrected_z
# over them all) for luck alone to put any bucket past it at most chance.TRIGGER_P of the time. Buckets overlap (styles
# and sizes cut the same trials), so the bar is conservative. Replayed over past results (2026-09-27): 1 of 17 kept
# results passes it (E0063, near_stronghold 0.36 over 9 trials, p 0.09 over its 11 buckets), and 3 of 13 promising
# ones (E0067, E0068, E0082).
def map_buckets(sprt: dict) -> tuple[list[dict], float]:
    (styles, repearl), areas = by_layout(sprt.get("per_style") or {}), sprt.get("per_area") or {}
    buckets = dict(styles)
    for name, group in zip(("near maps", "community maps"), near_groups(styles)):
        if len(group) > 1:
            buckets[name] = pooled(group)
    if repearl:
        buckets["repearled maps"] = repearl
    buckets |= {f"{k} tiles": v for k, v in areas.items()}
    scale = 1.0 if sprt.get("rotation") else 0.5  # a champion-only SPRT's per_map is pair points, 0-2 (driver)
    scores = [v * scale for v in (sprt.get("per_map") or {}).values() if isinstance(v, (int, float))]
    sd = (statistics.stdev(scores) if len(scores) > 2 else 0) or TRIAL_SD
    found = [{"name": name, "trials": v["trials"], "score": v["score"],
              "z": (v["score"] - 0.5) / (sd / math.sqrt(v["trials"]))}
             for name, v in buckets.items() if v.get("trials", 0) >= SPLIT_MIN_TRIALS and v.get("score") is not None]
    return found, chance.corrected_z(len(found))


# " SPLIT: helps on >=2400 tiles 0.55 (278, z +3.1), hurts on <=1024 tiles 0.44 (216, z -3.0) ..." when one map
# bucket (map_buckets) is clearly better than level and another clearly worse, past the corrected bar, else "".
def map_split_text(row: dict) -> str:
    buckets, bar = map_buckets(row.get("sprt") or {})
    helps, hurts = [b for b in buckets if b["z"] >= bar], [b for b in buckets if b["z"] <= -bar]
    if not (helps and hurts):
        return ""
    show = lambda items: ", ".join(f"{b['name']} {b['score']:.2f} ({b['trials']}, z {b['z']:+.1f})" for b in items)
    return (f" SPLIT: helps on {show(helps)}, hurts on {show(hurts)} (|z| {bar:.1f}+ over {len(buckets)} map buckets): "
            "consider a version conditioned on the map's size or style.")


# " [r500 longest 20/25, total 61, <=8 dragons 3/12 vs champion 18/26, total 55, <=8 dragons 1/12]": in games that
# went the distance, our longest/their longest, our total length and games we ended with 8 or fewer dragons,
# candidate then champion. Results from before 2026-09-26 have only the longest.
def longest_text(r500: dict) -> str:
    candidate, base = r500.get("candidate", {}), r500.get("base", {})
    if not candidate.get("games") or not base.get("games"):
        return ""
    def side(r: dict) -> str:
        rest = f", total {r['total']:g}, <=8 dragons {r['few']}/{r['games']}" if "total" in r else ""
        return f"{r['ours']:g}/{r['theirs']:g}{rest}"
    return f" [r500 longest {side(candidate)} vs champion {side(base)}]"


# " By map style: near 0.55 (800: near_trauma 0.60 ...), repearl 0.52 (400), mix 0.49 (1600); by size: <=1024 tiles
# 0.53 (1300), >=2400 tiles 0.50 (1100)." from the SPRT's `per_style` and `per_area` (results from before 2026-09-26
# have no `per_area`). Each near layout counts its plain and repearled maps (by_layout); repearl is the latter alone.
def style_text(styles: dict, areas: dict | None = None) -> str:
    sizes = "by size: " + ", ".join(f"{k} tiles {v['score']:.2f} ({v['trials']})" for k, v in areas.items()) \
        if areas else ""
    if not styles:
        return f" {sizes[0].upper()}{sizes[1:]}." if sizes else ""
    styles, repearl = by_layout(styles)
    parts = []
    for label, group in zip(("near", "community"), near_groups(styles)):
        if group:
            total = pooled(group)
            parts.append(f"{label} {total['score']:.2f} ({total['trials']}: "
                         + ", ".join(f"{k} {v['score']:.2f}" for k, v in group.items()) + ")")
        if label == "near" and repearl:
            parts.append(f"repearl {repearl['score']:.2f} ({repearl['trials']})")
    parts += [f"{k} {v['score']:.2f} ({v['trials']})" for k, v in styles.items() if not k.startswith("near_")]
    return " By map style: " + ", ".join(parts) + (f"; {sizes}" if sizes else "") + "."


# The chance the candidate's true Elo gain is above zero, from the SPRT's estimate and 95% range, or None.
# The chance a non-inferiority test's candidate is no worse than its lower bound `elo0` (normalized Elo), from the
# SPRT's nElo estimate: its standard error is NELO_PER_T / sqrt(2 x trials) (referee/sprt.py elo_estimate divides the
# score's deviation by sqrt(2 var)). E0209 (formula, 2400 pairs, nElo -4.2) reads 98% no worse than -15.
NELO_PER_T = 800 / math.log(10)  # referee/sprt.py's


def noloss_probability(report: dict) -> float | None:
    nelo, elo0, counts = report.get("nelo"), report.get("elo0"), report.get("pentanomial")
    if not (isinstance(nelo, (int, float)) and math.isfinite(nelo) and isinstance(elo0, (int, float))
            and isinstance(counts, list) and sum(counts) > 0):
        return None
    return 0.5 * (1 + math.erf((nelo - elo0) / (NELO_PER_T / math.sqrt(2 * sum(counts))) / math.sqrt(2)))


# results.jsonl's `rows` with each experiment id's latest row only, at that row's place: a result re-judged under a
# changed rule (driver.rejudge) is a later row for its id, which supersedes the earlier one for every reader.
def latest_rows(rows: list[dict]) -> list[dict]:
    last = {row.get("id"): k for k, row in enumerate(rows)}
    return [row for k, row in enumerate(rows) if last[row.get("id")] == k]


def gain_probability(report: dict) -> float | None:
    elo, low, high = report.get("elo"), report.get("elo_lo"), report.get("elo_hi")
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (elo, low, high)) or high <= low:
        return None
    return 0.5 * (1 + math.erf(elo / ((high - low) / 3.92) / math.sqrt(2)))


# Results not kept, 3 or more promotions old, that nothing has come back to: no later result on the same idea or
# stacked on it, and no idea citing the experiment. A no-code result counts only when the coder chose it (deliberate).
# Reviews either retest them on the current champion or say why not.
def unrevisited(rows: list[dict], ideas: list[dict]) -> list[dict]:
    later_kept = [sum(r["verdict"] == "kept" for r in rows[i + 1:]) for i in range(len(rows))]
    tested = [r["idea"].get("id") for r in rows]
    stale = []
    for i, row in enumerate(rows):
        if (row["verdict"] == "kept" or (no_code_result(row) and not deliberate(row)) or later_kept[i] < 3
                or row["idea"].get("id") in tested[i + 1:]
                or any(row["id"] in (later.get("stacked_on") or []) for later in rows[i + 1:])):
            continue
        # Another idea citing the result, or a review's note on the result's own idea (why its verdict still holds;
        # apply_review marks every note a review adds with review_note).
        cited = any(re.search(rf"\b{row['id']}\b", idea.get("hypothesis", "") + " ".join(idea.get("history") or []))
                    for idea in ideas if idea.get("id") != row["idea"].get("id")) or any(
            note.startswith("review ") and re.search(rf"\b{row['id']}\b", note)
            for idea in ideas if idea.get("id") == row["idea"].get("id") for note in idea.get("history") or [])
        if not cited:
            stale.append(row)
    return stale


# Whether a coding session did no work, from its own record: an empty final message, a short one that is a
# provider's limit notice (E0002-E0019's single turn was "You've hit your session limit", 65 characters; a coder's
# explained decline can mention a "CPU usage limit"), or at most one turn (no tool call) when the turn count was
# recorded. A coder that worked and handed in nothing says why in its final message.
def no_work(session: dict) -> bool:
    summary = (session.get("summary") or "").strip()
    turns = session.get("turns")
    limit = len(summary) < 200 and bool(re.search(r"session limit|usage limit|rate limit", summary, re.I))
    return not summary or limit or (isinstance(turns, int) and turns <= 1)


# The reason a coder gave for handing in no code: its `Blocked by:` line (loop/PROMPT.md) unless that says none, else
# the start of its final message (its first sentence is often just "nothing committed"), at most 300 characters.
def stated_reason(summary: str) -> str:
    blocked = re.search(r"Blocked by:\s*(.+)", summary)
    if blocked and not blocked.group(1).strip().lower().startswith("none"):
        return " ".join(blocked.group(1).split())[:300]
    return " ".join(summary.split())[:300]


# What a coder said its change delivers to the code that uses it (loop/PROMPT.md's `Delivery: <rate> <what reaches
# whom>` line, e.g. "Delivery: 0.000 census rays a dragon-turn heard by teammates"), as {"value", "line"} from the
# last such line, or None when it gave none or no number. driver.note_delivery sets it on the idea; held_back keeps
# ideas `after` it waiting while the value is 0 (M87).
DELIVERY = re.compile(r"\bDelivery\b[*_\s]*:[*_\s]*([-+]?\d[\d,]*(?:\.\d+)?|[-+]?\.\d+)\s*([^\n]*)", re.I)


def delivery(text: str) -> dict | None:
    found = DELIVERY.findall(text)
    if not found:
        return None
    value, rest = found[-1]
    line = " ".join(f"{value} {rest}".split())[:200]
    return {"value": float(value.replace(",", "")), "line": line}


# A post-mortem's (loop/POSTMORTEM.md step 3) verdict on whether its failure could have been known before coding, as
# ("yes" or "no", the rest of its line), from the last `Predictable: yes|no: <check or why>` in `text`; bold, a dash
# or a capital are fine. ("not stated", "") when it has none.
PREDICTABLE = re.compile(r"\bPredictable\b[*_\s]*:[*_\s]*(yes|no)\b[*_\s:;,.—–-]*(.*)", re.I)


def predictable(text: str) -> tuple[str, str]:
    found = PREDICTABLE.findall(text)
    return (found[-1][0].lower(), found[-1][1].strip()) if found else ("not stated", "")


# A no-code coder's shadow count of the decisions its change would alter: loop/PROMPT.md asks a stopping coder for a
# `Changed: <k> decisions in <n> games` line, and older summaries' prose reads the same ("changed 71 scored steps over
# 24 games", "would alter 17 moves in 32 games", "21052 changed actions in 952310 dragon-turns over 36 games").
# A count is at most 9 digits (commas allowed), so a huge one can't overflow chance.rate_ci.
COUNT = r"(\d(?:,?\d){0,8})"
CHANGED = re.compile(rf"\b(?:chang(?:ed|es)|alter(?:ed|s)?)\b(:?)\s*(?:on\s+)?{COUNT}\s+(?:[a-z-]+\s+){{0,3}}?"
                     rf"(?:in|over|across)\s+(?:all\s+)?{COUNT}\s+(?:[a-z-]+\s+){{0,2}}?games\b"
                     rf"|\b{COUNT}\s+changed\s+[a-z-]+(?:\s+[^.;]{{0,50}}?)?\s+(?:in|over|across)\s+{COUNT}\s+"
                     rf"(?:[a-z-]+\s+){{0,2}}?games\b", re.I)
# loop/PROMPT.md's line: under about 0.1 changed moves a game the SPRT can't see a change at any stakes.
SEEN_RATE = 0.1


# The (changed decisions, games) a no-code stop's summary counts, when that count says the change would act: its
# share of games lies above SEEN_RATE (chance.rate_ci), so a stop other than its firing rate rested on outcomes,
# which its post-mortem (driver.EARLY_ACTED) must check came from those decisions (MISSES 61: E0189 stopped on an
# average over every trip while its formula changed 71 steps in 24 games). The last `Changed:` line wins, else the
# first count; None without one. It counts at most one change a game, so a count packed into a few games reads too
# widely spread. driver.coder_text gives it the coder's whole final text where the summary was cut.
def stop_changed(summary: str) -> tuple[int, int] | None:
    found = [(m.group(1) == ":", int((m.group(2) or m.group(4)).replace(",", "")),
              int((m.group(3) or m.group(5)).replace(",", ""))) for m in CHANGED.finditer(" ".join(summary.split()))]
    if not found:
        return None
    lines = [f for f in found if f[0]]
    _, changed, games = lines[-1] if lines else found[0]
    return (changed, games) if games and chance.rate_ci(min(changed, games), games)[0] > SEEN_RATE else None


# Stop and go lines that may end an idea on missing evidence, by wording (MISSES 61; the orchestrator's queue audit of
# 2026-09-28 found 53 flawed stops in 81): "unless", a line that ends the idea unless evidence appears ("stop unless
# the lower bound > X", "build only if", "commit unless"); "not shown", a stop on failing to show a gain ("if guarded
# turns don't cut deaths, stop"); "go", a go line ("build if X"), which builds whether or not it fires. Each kind's
# reading is what the coder is told to check (driver.stop_lines_task). "Not shown" takes a negated gain ("don't cut",
# "no more often", "not lower", "if not"), not any negation or "fail": "stop if the rule doesn't fire in 35 games"
# and "stop if they fail more often at sign p <= 0.1" are good stops. The recommended form, "stop only if ...,
# otherwise build", is never flagged. A stop over the wrong rows or read only on local games can't be told from its
# words: loop/GENERALISE.md's rules and meta.stop_changed cover those.
GAIN = r"(?:cut|reduce|lower|raise|increase|improve|help|beat|gain|win|show|separate|matter|pay)\w*"
NOT_SHOWN = (r"(?:(?<=\bif )not\b|\bno (?:more often|better)\b|\b(?:is|are|was|were|isn't|aren't|wasn't|not)\s+(?:yet\s+)?shown\b"
             r"|\bnot\s+(?:lower|higher|above|below|better|worse|more|less|fewer|negative|positive)\b"
             rf"|\b(?:don't|doesn't|didn't|do not|does not|did not|won't|can't|cannot)\s+(?:clearly\s+)?{GAIN})")
STOP_VERB = r"\b(?:stop|abandon|drop)(?:s|p?ing)?\b"  # not "stopped": an idea's history of an earlier result
STOP_WORDING = {
    "unless": (re.compile(rf"(?:{STOP_VERB}|\bcommit nothing\b|\bno code\b)[^.;:]{{0,60}}?\bunless\b"
                          r"|\b(?:build|commit|hand(?:ed)? in|keep)\w*\s+(?:it\s+)?only\s+(?:if|when|at)\b"
                          r"|(?<!stop )\bonly\s+if\b[^.;]{0,160}?(?<!otherwise )(?<!else )\b(?:build|commit|hand in)\b"
                          r"|\bcommit\s+unless\b", re.I),
               "check whether this line ends the idea on missing evidence; if it does, read it as stop only if the "
               "evidence shows the opposite (the 95% upper bound below the line, or the other side at p <= 0.1), and "
               "otherwise build"),
    "not shown": (re.compile(rf"{STOP_VERB}[^.;]{{0,40}}?\bif\b[^.;]{{0,150}}?{NOT_SHOWN}"
                             rf"|\bif\b[^.;]{{0,150}}?{NOT_SHOWN}[^.;]{{0,80}}?{STOP_VERB}", re.I),
                  "check whether this line stops when a gain isn't shown, which is the SPRT's job; if it does, read "
                  "it as stop only if the opposite is shown at p <= 0.1, and otherwise build"),
    "go": (re.compile(r"\bbuild(?:\s+it)?\s+(?:if|when)\b|\bgate:\s", re.I),
           "a go line: if it doesn't fire, build anyway"),
}


# The (kind, sentence) pairs of `text` that STOP_WORDING flags, in order, one per sentence (its first kind).
def stop_wording(text: str) -> list[tuple[str, str]]:
    found = []
    for sentence in re.split(r"(?<=[.;])\s+", " ".join(text.split())):
        kind = next((k for k, (pattern, _) in STOP_WORDING.items() if pattern.search(sentence)), None)
        if kind:
            found.append((kind, sentence))
    return found


# Bare hand thresholds in a new idea's rule (MISSES 56; REFLECT 2026-09-28 14:51 F1: I651's "from r250" and I662's
# "15+ long ... before r400", both the orchestrator's own): a round, length, distance or map size written as a number
# ("from round N", "before rN", "N+ long", "length >= N", "within N tiles", "under N tiles", "at least N rounds").
# Checked are the title and the hypothesis sentences that state a rule, not evidence: a sentence counting or watching
# games ("died", "per game", a result or game id), a test's case, a statistic or a measurement is left alone, and so
# is a number named as what the idea replaces ("not whenever the king is within 4 steps") or another idea's rule
# restated ("Same change as I580: ..."). Round 0, 1, 499 and 500 are the game's ends, and lengths and step counts up to
# 3 are the game's own (a sprint takes length 3). A threshold named as an observed quantity ("rounds left < path to
# the king") has no number and never matches. On the backlog of 2026-09-28 (280 todo ideas) it flagged 14; one (I640,
# a stack naming its part's round) adds no hand value of its own. Over the last 100 ideas then (I614-I713) it refused
# 11, 5 of them measurements or restatements (I626, I670, I671, I691, I698); with the statistic, doom and restated
# exemptions 6, each a rule's own number but I640. A round may be approximate ("from about r250", "after ~r300") and a
# game phase named as a trigger ("from the middle game", "in the late game", "by the endgame") is a hand round in words
# (REFLECT 2026-09-30 09:12 F2: I1064 was queued as "Trickle-feed the king from the middle game", Stockfish's "from
# about r250", and sig asked whether that was a magic number); a phase word in evidence ("our king is short in the late
# game, p 0.001") is left alone as every evidence sentence is.
THRESHOLD = re.compile(r"""\b(?:from|before|after|until|past|since)\s+(?:(?:about|around|roughly|approximately)\s+|~\s*)?
     (?:round\s+|r~?)
     (?!(?:0|1|499|500)\b)\d+\b
    |\b(?:from|before|after|until|past|since|once|by|in|during|through)\s+(?:the\s+)?
     (?:(?:early|middle|mid|late)[\s-]?game|end[\s-]?game)\b
    |\b(?:round\s+|r)(?!(?:0|1|499|500)\b)\d+\s*\+
    |\b(?![0-3]\b)\d+\s*\+\s*(?:long|segments?|steps?)\b|\b\d+\s*\+\s*(?:tiles?|rounds?)\b
    |\blength\s*(?:>=?|≥|of\s+at\s+least)\s*(?![0-3]\b)\d+\b
    |\bwithin\s+\d+\s+(?:tiles?|steps?|cells?)\b
    |\b(?:under|over|below|above|more\s+than|less\s+than|fewer\s+than|at\s+least|at\s+most)\s+\d+\s+
     (?:long|tiles?|rounds?|segments?|steps?)\b""", re.I | re.X)
THRESHOLD_RULE = re.compile(r"\b(?:build|change|trigger|response|rule|gate|instead|switch|when|while|once)\b", re.I)
NOT_A_RULE = (r"\b(?:report|measure|siblings|quantity|stop|re-?check|retest|losses|currently|today|champion's|old|"
              r"replac\w*)\b|%|\bE\d{4}\b|\b\d{5,}\b")
# A statistic ("p 0.027", "n 24", "z 2.35", "se 0.4", "p about 3e-17") or a death's cause ("doomed from r14") marks a
# measurement (I698, I671 on the backlog of 2026-09-28).
EVIDENCE = re.compile(NOT_A_RULE + r"|\b(?:died|dies|deaths?|lost|won|measured|found|watched|walked|replays?|"
                      r"scenarios?|tests?|boundary|example|caveat|play|doom\w*)\b|\bper game\b|\d (?:a|per) game\b"
                      r"|\b\d+ (?:[\w-]+ ){0,3}games\b"
                      r"|\b(?:p|n|z|se)\s*(?:=|<=?|>=?|≤|≥|about)?\s*-?(?:\d+(?:\.\d+)?|\.\d+)(?:e-?\d+)?\b", re.I)
# Another team's play described (OBSERVED: a ladder rank or the leaders) in a sentence stating no rule of ours
# (OUR_RULE): "Stockfish (now rank 1) grows one king from about r250 while its swarm keeps breeding" (I1064), "if the
# leaders' line leads from about r250" (I1077). A sentence that builds, sets or copies with it is still a rule ("set
# rank 1's mergeMinUnits 8->4", "copy the leaders and feed from about r250"), and no value swap is exempted so.
OBSERVED = re.compile(r"\brank \d+\b|\bthe leaders\b|\bleaders'", re.I)
OUR_RULE = re.compile(r"\b(?:build|change|set|copy|port|switch|make|use)s?\b", re.I)
NAMED_OLD = re.compile(r"(?:\bnot\b|\binstead of\b|\brather than\b|\bno longer\b)[^.;,]{0,40}$", re.I)
# Another idea's rule restated ("Same change as I580: ..."), whose thresholds are that idea's (I626, I670, I691 on the
# backlog of 2026-09-28): a sentence saying so, and the title of an idea whose title or hypothesis's first sentence
# does, aren't this idea's own rule. NAMED_OLD reads only the words before a number, and I626's title names the
# idea after it.
RESTATED = re.compile(r"\bsame change as I\d+\b", re.I)
# The marker an idea gives its reason with (loop/GENERALISE.md), which lets it keep a bare threshold.
THRESHOLD_REASON = re.compile(r"\bbare thresholds? because\b", re.I)
# A hand cutoff behind a param name (MISSES 96: I538's "dragons shorter than P.<searchMinLength> don't search" put
# E0343's 8 in the bot with no number in its text): a rule comparing with a param the champion's bot/params.hpp
# lacks, so the idea adds its value: "shorter than P.<name>", "within P.<name>", ">= P.<name>", "P.<name> or more",
# "P.<name>+ long". A rule on an existing param reuses a number already there (a formula for it is its own idea); a
# param times or over something ("under P.<slack> * path") is a formula's coefficient, and one the text defines by a
# formula (param_formula) or a CPU budget (param_budget) is no hand cutoff. With the champion's params unreadable, no
# param compare is flagged.
PARAM = r"P\.[A-Za-z_]\w*"
NOT_TIMES = r"(?![\w.]|\s*(?:[*×/]|x\s|times\b))"
PARAM_THRESHOLD = re.compile(rf"""\b(?:shorter|longer|smaller|larger|bigger|fewer|more|less|greater|higher|lower)\s+than
    \s+({PARAM}){NOT_TIMES}
    |\b(?:under|over|below|above|within|beyond|past|at\s+least|at\s+most|up\s+to|from|until|before|after)\s+
     (?:length\s+|round\s+)?({PARAM}){NOT_TIMES}
    |(?:>=?|<=?|≥|≤)\s*({PARAM}){NOT_TIMES}
    |({PARAM})\s*(?:\+\s*(?:long|segments?|steps?|tiles?|rounds?)\b
     |\s+or\s+(?:more|longer|fewer|less|shorter|higher|lower|above|below|over|under|later|earlier)\b)""", re.X)
# A CPU budget's name ends as one does (searchWorkLimit, searchClockLimit, searchMaxDepth, maxNodes), a camelCase word
# and not a substring: trapLineDepth, a line's length, is no budget.
PARAM_BUDGET = re.compile(r"(?:^|(?<=[a-z0-9]))(?:(?:Work|Clock|Nodes?|Budget|Cpu|CPU|Iterations?)(?:Limit|Cap|Max)?"
                          r"|Max(?:Depth|Nodes|Work|Iterations)|Depth(?:Limit|Cap))$")


def param_budget(name: str) -> bool:
    bare = name.removeprefix("P.")
    return bool(PARAM_BUDGET.search(bare[:1].upper() + bare[1:]))


# Whether `text` defines param `name` ("P.<name>") by a formula: "P.<name> = <...>" whose right side is a quantity or
# has an operator ("the rammer's length", "2 * rounds left"), not a bare number ("= 8"); or in words, "where P.<name>
# is <...>", "P.<name> is the <...>", "P.<name>, the <share, length ...> at which <...>" (I471's "P.<feedMinShare>, the
# share at which a drop to the king beats ..."), or an aside in brackets after it, an offset allowed, with three words
# of prose ("(start 2, a pearl's worth of steps: ...)", I528; "+ 1 (it can't split out)", I934), not "(8)" or
# "(start 8)".
def param_formula(name: str, text: str) -> bool:
    n = re.escape(name)
    sides = [found.split(". ")[0] for found in re.findall(rf"{n}\s*:?=\s*([^;]{{1,120}})", text)]
    if any(re.search(r"[*×/^]", side) or not re.match(r"-?[\d.]+(?!\w)", side) for side in sides):
        return True
    if re.search(rf"\bwhere\s+{n}\s+(?:is|equals|means)\b|{n}\s+(?:is|equals|means)\s+(?:the|a|an)\s"
                 rf"|{n},\s+(?:the|a|an)\s+[\w' -]{{1,40}}?\s(?:(?:at|below|above|under|over|for|from|past)\s+)?"
                 r"(?:which|where|when)\b", text):
        return True
    asides = re.findall(rf"{n}(?:\s*[+-]\s*\d+)?\s*\(([^()]{{1,200}})\)", text)
    return any(len(re.findall(r"[A-Za-z][A-Za-z']+", re.sub(r"\b(?:start|starts|default|initially|about)\b", "",
                                                            aside))) >= 3 for aside in asides)


# A param's hand value swapped for another, "mergeMinUnits 8->4", "P.splitAtLength from 6 to 5": a number ported or
# picked, not a rule over what the bot sees (REFLECT 2026-09-29 20:42 F11: I968 ported feederking's four values and sig
# asked "shoudlnt this idea be a formula??"; 12 waiting backlog rows carried one on 30 Sep). A switch's true->false is no number.
VALUE_SWAP = re.compile(r"\b(?:P\.)?([a-z][a-z0-9]*[A-Z][A-Za-z0-9]*)\s*(?:-?\d+(?:\.\d+)?\s*(?:->|→)|from\s+-?\d+(?:\.\d+)?\s+to)"
                        r"\s*-?\d+(?:\.\d+)?(?![\w.])")
# The evidence a swap's clause may name: EVIDENCE but for "champion's" ("set the champion's x 8->4" is still a rule),
# plus a screen port's "moves off the champion's values", the entry's diff it hints from (screenport.proposal).
SWAP_EVIDENCE = re.compile(EVIDENCE.pattern.replace("champion's|", "", 1) + r"|\bmoves off the champion's values\b",
                           re.I)


# The (threshold, sentence) pairs of `idea`'s rule that THRESHOLD or PARAM_THRESHOLD flags, and any VALUE_SWAP in its
# title or a sentence that isn't evidence (not in a tuning idea, whose values SPSA picks); [] when its text gives
# THRESHOLD_REASON.
def bare_thresholds(idea: dict) -> list[tuple[str, str]]:
    title, hypothesis = flat(idea.get("title", "")), flat(idea.get("hypothesis", ""))
    if THRESHOLD_REASON.search(f"{title} {hypothesis}"):
        return []
    before, _, after = hypothesis.partition("Options:")  # the other options' sentences aren't this idea's rule
    sentences = re.split(r"(?<=[.;])\s+", before) + [s for s in re.split(r"(?<=[.;])\s+", after)
                                                     if not OPTION.search(s)]
    restated = RESTATED.search(title) or RESTATED.search(sentences[0])
    rules = [title] if not (re.search(NOT_A_RULE, title, re.I) or restated) else []
    rules += [s for s in sentences if THRESHOLD_RULE.search(s) and not EVIDENCE.search(s) and not RESTATED.search(s)
              and not (OBSERVED.search(s) and not OUR_RULE.search(s))]
    found = [(m.group(0), s) for s in rules for m in THRESHOLD.finditer(s) if not NAMED_OLD.search(s[:m.start()])]
    known = None  # the champion's params.hpp, read at the first param compare
    for sentence in rules:
        for m in PARAM_THRESHOLD.finditer(sentence):
            name = next(filter(None, m.groups()))
            if NAMED_OLD.search(sentence[:m.start()]) or param_budget(name) \
                    or param_formula(name, f"{title} {hypothesis}"):
                continue
            known = champion_params() if known is None else known
            if known and not re.search(rf"\b{re.escape(name[2:])}\b", known):
                found.append((m.group(0), sentence))
    if idea.get("kind") != "tune":
        # evidence is judged on the swap's clause, from the last ":" before it: "worth having in today's bot: this
        # idea sets mergeMinUnits 8->4" states the rule after naming a day
        found += [(m.group(0), s) for s in [title] * (not restated) + [s for s in sentences if not RESTATED.search(s)]
                  for m in VALUE_SWAP.finditer(s)
                  if not SWAP_EVIDENCE.search(s[s.rfind(":", 0, m.start()) + 1:m.end()])
                  and not NAMED_OLD.search(s[:m.start()]) and not param_budget(m[1])
                  and not param_formula(f"P.{m[1]}", f"{title} {hypothesis}")]
    return found


# The bare thresholds in an orchestrator's note on an idea (driver.orchestrator_note; REFLECT 2026-09-29 12:53 F5: its
# 10:57 note on I915 set "maps of 1024 tiles or fewer" as the idea's circumstance, and sig asked whether it would
# hard-code the map size): THRESHOLD's numbers and a map-size band (SIZE_BAND: "1024 tiles or fewer", "at most 1024
# tiles", "<=1024 tiles", "1025+ tiles"), in any sentence that isn't evidence (EVIDENCE: a count, statistic or result,
# as bare_thresholds reads an idea) or a restated old rule, since a coder follows a note's every sentence as a
# correction. [] when it says "bare threshold because". Of the live backlog's 8 orchestrator notes naming 1024 or
# 1025 tiles on 29 Sep (I317, I338, I413, I458, I462, I474, I717, I915) it flags I915's two sentences alone, and of
# all its 815 notes 14 (I651's "from r320" among them, MISSES 56's own case).
SIZE_BAND = re.compile(r"""\b\d+\s*tiles?\s+or\s+(?:fewer|less|smaller|more|larger|bigger)\b
    |(?:\b(?:at\s+most|at\s+least|up\s+to|under|over|below|above)\s+|<=?\s*|>=?\s*|≤\s*|≥\s*)\d+\s*tiles?\b""",
                       re.I | re.X)


def note_thresholds(text: str) -> list[tuple[str, str]]:
    text = flat(text)
    if THRESHOLD_REASON.search(text):
        return []
    found = []
    for sentence in re.split(r"(?<=[.;])\s+", text):
        if EVIDENCE.search(sentence) or RESTATED.search(sentence):
            continue
        for pattern in (THRESHOLD, SIZE_BAND):
            found += [(m.group(0), sentence) for m in pattern.finditer(sentence)
                      if not NAMED_OLD.search(sentence[:m.start()])]
    return found


# Remedies that only remove a mechanism's bad outcome (MISSES 71: every pocket finding became an avoidance rule, and
# nobody proposed a pearl room as a factory whose farmer splits and sends the child out through a known portal). Every
# new idea's hypothesis carries an "Options:" line: MIN_OPTIONS or more numbered candidate remedies for its finding
# from different framings, "(1) ... (2) ... (3) ...", a waiting idea standing as one ("I524 already covers the farm
# version"), then which one this idea is and why. Ideas queued before 2026-09-28 have none (1 of 282 todo ideas had
# the word) and are left as they are.
OPTION = re.compile(r"\((\d+)\)")
MIN_OPTIONS = 3
# Queued avoidance-only ideas each review gives an Options note (PROMPT's Options pass; REFLECT 2026-09-28 17:52 F3:
# 1 of 282 todo ideas had an Options line, 46 titles named pockets), few enough to fit beside its other steps.
OPTIONS_PASS = 3


# How many distinct numbered options `idea`'s "Options:" line lists, 0 without one.
def options_count(idea: dict) -> int:
    _, label, after = flat(idea.get("hypothesis", "")).partition("Options:")
    return len(set(OPTION.findall(after))) if label else 0


# The final text of the post-mortem saved for a result (runs/E####/postmortem.json, the Claude coder's JSON output,
# driver.run_postmortem), "" when it gave no result, or None when none was saved.
def postmortem_text(run_dir: Path) -> str | None:
    path = run_dir / "postmortem.json"
    if not path.exists():
        return None
    try:
        output = json.loads(path.read_text())
    except (OSError, ValueError):
        return ""
    results = [item for item in (output if isinstance(output, list) else [output])
               if isinstance(item, dict) and isinstance(item.get("result"), str)]
    return results[-1]["result"] if results else ""


PREDICTABLE_ROWS = 20


# stats.md's "Predictable failures" section (MISSES 32): the latest PREDICTABLE_ROWS results not kept, each with its
# post-mortem's Predictable line, and how many said yes, no, nothing, or had no post-mortem saved (no-code and error
# results get none; a native post-mortem saves only native.py followups' marker, read as not stated). Reviews turn a
# check that recurs into a rule.
def predictable_lines(rows: list[dict], runs: Path) -> list[str]:
    counts: Counter = Counter()
    entries = []
    for row in [r for r in rows if r["verdict"] != "kept"][-PREDICTABLE_ROWS:]:
        text = postmortem_text(runs / row["id"])
        verdict, rest = ("no post-mortem saved", "") if text is None else predictable(text)
        counts[verdict] += 1
        said = f"{verdict}: {rest[:300]}" if rest else verdict
        entries.append(f"- {row['id']} [{row['idea']['id']}] {flat(row['idea']['title'])[:100]} ({row['verdict']}): {said}")
    lines = ["", "## Predictable failures (post-mortems' `Predictable:` lines, MISSES 32)", "",
             f"Whether each of the latest {PREDICTABLE_ROWS} results not kept could have been known before coding, "
             "and by which check, in its post-mortem's own words. A check that recurs belongs in the design check or "
             "the idea-writing rules.", "",
             f"Of {len(entries)}: " + ", ".join(f"{counts[k]} {k}" for k in
                                               ("yes", "no", "not stated", "no post-mortem saved")) + ".", ""]
    return lines + (entries or ["- none yet"])


# A result with no commit to test: "no code" (from 2026-09-26) or, before, "discarded" with reason "no commit".
def no_code_result(row: dict) -> bool:
    return row.get("verdict") == "no code" or (row.get("reason") or "").startswith("no commit")


# A no-code result the coder chose and explained (it measured the idea and named what blocks it, like E0071's missing
# estimate of the opponent's longest), as opposed to a session that did no work (no_work: session limits).
def deliberate(row: dict) -> bool:
    return no_code_result(row) and not no_work(row.get("session") or {})


# ", 1968 pairs" for a result's SPRT length (what the test cost), or nothing without an SPRT.
def pairs_text(row: dict) -> str:
    pairs = (row.get("sprt") or {}).get("pairs")
    return f", {pairs} pairs" if pairs else ""


# Whether a result played games: an SPRT, an identical-games check, or a tune's SPSA (its own games, even when it kept
# every value). A no-code or error result played none.
def played(row: dict) -> bool:
    session = row.get("session") or {}
    return bool((row.get("sprt") or {}).get("pairs") or (row.get("identity") or {}).get("maps")
                or (session.get("provider") == "tuner" and session.get("exit") == 0))


NEVER_PLAYED_SHOWN = 15


# 4 for "I004"; None for an id not of that form.
def idea_number(idea_id: str | None) -> int | None:
    match = re.fullmatch(r"I(\d+)", idea_id or "")
    return int(match.group(1)) if match else None


# What the loop never ran leaves no trace, so this lists it (MISSES 24): waiting ideas (todo or followup) that no
# result played games for, oldest first, as (idea, age in days or None, the ids of its results), and the categories
# none of whose ideas ever played. Ideas carry no timestamp, but ids are handed out in order, so an idea existed by
# the time the first result on an idea numbered the same or later finished: its age is at least `now` minus that, and
# None when there is no such result yet (a new idea).
def never_played(rows: list[dict], ideas: list[dict], now: float) -> tuple[list[tuple[dict, float | None, list[str]]],
                                                                            list[tuple[str, int]]]:
    finished = [(n, row["finished"]) for row in rows if (n := idea_number(row["idea"].get("id"))) is not None
                and isinstance(row.get("finished"), (int, float))]
    played_ids = {row["idea"].get("id") for row in rows if played(row)}
    waiting = []
    for idea in ideas:
        if idea.get("status") not in ("todo", "followup") or idea["id"] in played_ids:
            continue
        own = idea_number(idea["id"])
        since = [at for n, at in finished if own is not None and n >= own]
        age = (now - min(since)) / 86400 if since else None
        waiting.append((idea, age, [row["id"] for row in rows if row["idea"].get("id") == idea["id"]]))
    waiting.sort(key=lambda item: (item[1] is None, -(item[1] or 0), idea_number(item[0]["id"]) or 0))
    categories = defaultdict(list)
    for idea in ideas:
        categories[idea.get("category") or "?"].append(idea)
    unplayed = sorted((name, sum(i.get("status") in ("todo", "followup") for i in group))
                      for name, group in categories.items() if not any(i["id"] in played_ids for i in group))
    return waiting, unplayed


# never_played as stats.md's section.
def never_played_lines(rows: list[dict], ideas: list[dict], now: float) -> list[str]:
    waiting, categories = never_played(rows, ideas, now)
    lines = ["", f"## Never played: {len(waiting)} waiting ideas no result played games for, oldest first", ""]
    for idea, age, ids in waiting[:NEVER_PLAYED_SHOWN]:
        since = f"waiting at least {age:.1f} days" if age is not None else "new"
        tried = f"; results without games: {', '.join(ids)}" if ids else ""
        lines.append(f"- {idea['id']} [{idea.get('category')}, priority {idea.get('priority')}] {flat(idea.get('title') or '')}: "
                     f"{since}{tried}")
    if len(waiting) > NEVER_PLAYED_SHOWN:
        lines.append(f"- and {len(waiting) - NEVER_PLAYED_SHOWN} more")
    lines += ["", "Categories with no idea ever played (waiting ideas in each):", ""]
    lines += [f"- {name}: {count}" for name, count in categories] or ["- none"]
    return lines


# One finding split into several waiting ideas (MISSES 4 and 41): the 27 Sep 02:35 audit found that in the opening we
# reach the rich ground after the opponent, and queued it as four map-shaped remedies (I355, and "same change as" rows
# merged into I191, I294 and I335), each needing its own SPRT; I191, I294 and I335 had come from three earlier audits.
# Two waiting ideas of one category are siblings when their words (title, hypothesis and the text of rows merged into
# them, each word weighted by how rare it is in the waiting backlog) overlap by SIBLING_SIMILARITY or more and one
# audit wrote both, counting the audits of rows merged into each ("audit:<id>" sources), or by SIBLING_ANY_SOURCE or
# more whoever wrote them: two remedies for one measured quantity cite the same quantity, code and games, where a run's
# unrelated findings don't (I252, a ram guard from that audit's F4, is another category and scores 0.16 against I191 on
# the backlog of 27 Sep 12:00, where the four score 0.20-0.45 with each other and I094 0.38 against I335 across
# sources). The lower bar is for audits only: a post-mortem's or follow-on's up to 3 follow-ups are distinct changes to
# the same code by design, and 6 of the 9 groups this check first listed on that backlog were such pairs. Ideas that re-test a built
# candidate (SIBLING_RETESTS) and the generators' one-idea-per-item rows (SIBLING_SKIP) share a template, not a
# finding, and are left out. A link is acknowledged and not listed when either idea names the other (idea_names), or a
# third waiting idea names both: a stack waiting on them (I375, "after" I191 and I355) or one saying how it differs
# (I374: "I355 is the narrow version ... they stay separate", and it builds on I094). So a review that keeps members
# apart clears a group with a note on each naming the others. A waiting idea naming two or more of a cluster's members
# is shown beside it, with the members it waits on: it is never a merge target for them.
SIBLING_SIMILARITY = 0.2
SIBLING_ANY_SOURCE = 0.35
SIBLING_RETESTS = ("retest", "recheck", "quickfix")
SIBLING_SKIP = ("recheck:", "sweep:", "screen-port:", "rescreen:", "tunesplit:")
SIBLING_SHOWN = 12
SIBLING_TITLE_HEAD = 15
INTAKE_NOTE = "flagged at intake as a possible duplicate or complement of"
_STOPWORDS = frozenset(
    "a an and are as at be but by can do does for from has have if in into is it its no not of on one only or our so "
    "than that the their them then they this to two us was we were what when which while who with same change idea "
    "each more most all any also after before per over up out".split())
_IDEA_ID = re.compile(r"\bI\d{3,}\b")


# An idea's own text (title, hypothesis, history), where sibling_clusters looks for the ids it names.
def own_text(idea: dict) -> str:
    return " ".join([str(idea.get("title") or ""), str(idea.get("hypothesis") or ""),
                     *map(str, idea.get("history") or [])])


# The words that carry an idea's meaning, for sibling_clusters: lower case, experiment and idea ids and stopwords out.
def idea_words(text: str) -> Counter:
    words = re.findall(r"[a-z][a-z0-9_.:-]*[a-z0-9]|r\d+(?:-\d+)?", text.lower())
    return Counter(w for w in words if w not in _STOPWORDS and not re.fullmatch(r"[ie]\d+", w))


# Unit vectors of `texts` (id -> text) weighted by log term frequency times how rare each word is among `texts`;
# a word only one idea uses can't link two, so it is left out.
def word_vectors(texts: dict[str, str]) -> dict[str, dict[str, float]]:
    counts = {i: idea_words(t) for i, t in texts.items()}
    df = Counter(w for c in counts.values() for w in c)
    vectors = {}
    for i, c in counts.items():
        v = {w: (1 + math.log(n)) * math.log(len(counts) / df[w]) for w, n in c.items() if df[w] > 1}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        vectors[i] = {w: x / norm for w, x in v.items()}
    return vectors


# Cosine similarity of two word_vectors entries.
def similarity(a: dict[str, float], b: dict[str, float]) -> float:
    return sum(x * b.get(w, 0.0) for w, x in a.items())


# Each of the `waiting` ideas' finding runs (its source and those of rows merged into it, through merge_target) and
# its text with theirs, for sibling_clusters.
def idea_runs(ideas: list[dict], waiting: set[str]) -> tuple[dict[str, set[str]], dict[str, str]]:
    by_id = {i["id"]: i for i in ideas}
    runs: dict[str, set[str]] = defaultdict(set)
    texts: dict[str, str] = defaultdict(str)
    for idea in ideas:
        kept = merge_target(idea["id"], by_id)
        if kept not in waiting:
            continue
        texts[kept] += f" {idea.get('title') or ''} {idea.get('hypothesis') or ''}"
        source = str(idea.get("source") or "")
        if ":" in source and not source.startswith(SIBLING_SKIP):
            runs[kept].add(source)
    return runs, texts


# The waiting ideas `idea` names, from `waiting` (id -> row): by id in its own text (own_text) or its "after", or by a
# title's head (the part before a colon, or the whole title, of SIBLING_TITLE_HEAD+ characters: "Opening portal
# scouts"), as a worker names a sibling in its own output before either has an id. An intake note (INTAKE_NOTE) names
# its match without deciding anything, so it doesn't count: the review still lists the pair until someone does.
def idea_names(idea: dict, waiting: dict[str, dict]) -> set[str]:
    text = own_text({**idea, "history": [h for h in idea.get("history") or [] if not str(h).startswith(INTAKE_NOTE)]})
    ids = set(_IDEA_ID.findall(text)) | {str(a) for a in idea.get("after") or []}
    lower = text.lower()
    heads = {i: str(row.get("title") or "").split(":", 1)[0].strip().lower() for i, row in waiting.items()}
    titled = {i for i, head in heads.items() if len(head) >= SIBLING_TITLE_HEAD and head in lower}
    return (ids & set(waiting) | titled) - {idea["id"]}


# Whether sibling_pairs scores idea `i`: waiting (todo or followup), or with `in_flight` also being coded (trying:*),
# and neither a retest nor a generator's row (SIBLING_RETESTS, SIBLING_SKIP).
def sibling_scored(i: dict, in_flight: bool = False) -> bool:
    status = str(i.get("status") or "")
    return (status in ("todo", "followup") or in_flight and status.startswith("trying:")) \
        and i.get("category") not in SIBLING_RETESTS and not str(i.get("source") or "").startswith(SIBLING_SKIP)


# The linked pairs of waiting ideas (todo or followup) that look like one finding split (see SIBLING_SIMILARITY), as
# (a, b, score) with a before b by id number, and each scored idea's idea_names; with `only`, just the pairs with a
# member in it (intake_siblings' new rows, scored against the whole backlog and the ideas being coded).
# sibling_clusters groups them.
def sibling_pairs(ideas: list[dict], only: set[str] | None = None) -> tuple[list[tuple[str, str, float]],
                                                                            dict[str, set[str]]]:
    by_id = {i["id"]: i for i in ideas}
    waiting = {i["id"] for i in ideas if sibling_scored(i, in_flight=only is not None)}
    runs, texts = idea_runs(ideas, waiting)
    vectors = word_vectors({i: texts[i] for i in waiting})
    named = {i: idea_names(by_id[i], {w: by_id[w] for w in waiting}) for i in waiting}
    together = {frozenset((a, b)) for c in waiting for a in named[c] for b in named[c] if a != b}
    order = sorted(waiting, key=lambda i: idea_number(i) or 0)
    pairs = []
    for n, a in enumerate(order):
        for b in order[n + 1:]:
            if only is not None and a not in only and b not in only \
                    or by_id[a].get("category") != by_id[b].get("category") or b in named[a] or a in named[b] \
                    or frozenset((a, b)) in together:
                continue
            audit = any(run.startswith("audit:") for run in runs[a] & runs[b])
            if (score := similarity(vectors[a], vectors[b])) >= (SIBLING_SIMILARITY if audit else SIBLING_ANY_SOURCE):
                pairs.append((a, b, score))
    return pairs, named


# The clusters of waiting ideas that look like one finding split (sibling_pairs), as (members, ideas naming two or
# more of them), largest first; each list is sorted by id number.
def sibling_clusters(ideas: list[dict]) -> list[tuple[list[str], list[str]]]:
    pairs, named = sibling_pairs(ideas)
    parent = {i: i for i in named}

    def root(i: str) -> str:
        while parent[i] != i:
            i = parent[i]
        return i

    for a, b, _ in pairs:
        parent[root(b)] = root(a)
    order = sorted(named, key=lambda i: idea_number(i) or 0)
    groups: dict[str, list[str]] = defaultdict(list)
    for i in order:
        groups[root(i)].append(i)
    clusters = []
    for members in (g for g in groups.values() if len(g) > 1):
        general = [i for i in order if i not in members and len(named[i] & set(members)) >= 2]
        clusters.append((members, general))
    return sorted(clusters, key=lambda c: (-len(c[0]), idea_number(c[0][0]) or 0))


# sibling_clusters as stats.md's section, which reviews act on (the review prompt, "Split findings").
def sibling_lines(ideas: list[dict]) -> list[str]:
    clusters = sibling_clusters(ideas)
    by_id = {i["id"]: i for i in ideas}
    groups = f"{len(clusters)} group{'' if len(clusters) == 1 else 's'}"
    lines = ["", f"## Split findings: {groups} of waiting ideas about the same finding that don't name each other "
             "(meta.sibling_clusters)", "",
             "Merge only true duplicates (the same change; one mechanism asked for under different map conditions is the "
             "same change, merged into one idea whose condition covers every case). Otherwise add a history note to each member naming the "
             "others and saying which general quantity it moves and how it differs, or add a stacked general version "
             "(\"after\" on the members, built once each is tested). Never merge a member into an idea that waits on "
             "it; a member that names the others is not listed again.", ""]
    for members, general in clusters[:SIBLING_SHOWN]:
        line = "- " + "; ".join(f"{i} [{by_id[i].get('category')}, priority {by_id[i].get('priority')}] "
                                f"{flat(by_id[i].get('title') or '')[:90]}" for i in members)
        notes = []
        for g in general:
            waits = [m for m in members if m in {merge_target(str(a), by_id) for a in by_id[g].get("after") or []}]
            notes.append(f"{g} (waits on {', '.join(waits)}: a stack, not a merge target)" if waits else g)
        lines.append(line + (f". Also named together by {', '.join(notes)}" if notes else ""))
    if len(clusters) > SIBLING_SHOWN:
        lines.append(f"- and {len(clusters) - SIBLING_SHOWN} more")
    return lines if clusters else lines[:3] + ["- none"]


# A result's hard cutoffs added and removed (loop/cutoffs.py): its `cutoffs` field, or for a result recorded before the
# field (2026-09-27) or its params.hpp counts, read from its commits in `repo` (about 0.04 s a result, champion
# inventories shared through `cache`); None when it has no candidate or they can't be read.
def result_cutoffs(row: dict, repo: Path, cache: dict[str, list]) -> dict | None:
    saved = row.get("cutoffs")
    if saved and "error" in saved or not row.get("candidate"):
        return None
    if saved and "n_added_params" in saved:
        return saved
    import cutoffs
    base, head = (row.get("evaluation") or {}).get("base") or row.get("base"), row["candidate"]
    if not base:
        return None
    try:
        for rev in (base, head):
            if rev not in cache:
                cache[rev] = cutoffs.inventory(cutoffs.read_rev(repo, rev))
    except (subprocess.SubprocessError, OSError, ValueError, IndexError):
        return None
    return cutoffs.summary(cache[base], cache[head])


# stats.md's hard-cutoff section (MISSES 56): verdicts and mean Elo of results that removed more cutoffs than they
# added, added more, or neither (tunes, which only change values, and most speed ideas land there), counted twice:
# over code compares and constants, and over params.hpp numbers (most added cutoffs are new params, which a
# formula's coefficient also is); and each champion's count, so whether formulas beat cutoffs, and whether the bot's
# cutoffs are falling, shows.
def cutoff_lines(rows: list[dict], repo: Path | None = None) -> list[str]:
    cache: dict[str, list] = {}
    kinds = {"code compares and constants": {"removed": [], "added": [], "neither": []},
             "params.hpp numbers": {"removed": [], "added": [], "neither": []}}
    counts, unread = [], 0
    for row in rows:
        found = result_cutoffs(row, repo or REPO, cache)
        if found is None:
            unread += bool(row.get("candidate"))
            continue
        params = found["n_added_params"] - found["n_removed_params"]
        for kind, net in (("code compares and constants", found["n_added"] - found["n_removed"] - params),
                          ("params.hpp numbers", params)):
            kinds[kind]["added" if net > 0 else "removed" if net < 0 else "neither"].append(row)
        if row["verdict"] == "kept":
            counts.append(f"{row['id']} {found['count']}")
    lines = ["", "## Hard cutoffs added and removed (loop/cutoffs.py, MISSES 56)", "",
             "Results with a candidate, by whether it removed more hard cutoffs than it added (a rule over what the "
             "dragon observes in place of a bare number), added more, or neither, counted apart for code compares "
             "and constants (`w.round >= 350`) and for params.hpp numbers (a new param may be a threshold or a "
             "formula's coefficient). Mean Elo is over those with an SPRT. Kept rates leave out noloss, formula and "
             "targeted results, kept for no loss rather than a gain.", ""]
    for kind, groups in kinds.items():
        lines.append(f"By {kind}:")
        for name, members in groups.items():
            elos = [r["sprt"]["elo"] for r in members if isinstance((r.get("sprt") or {}).get("elo"), (int, float))]
            mean = f"mean Elo {sum(elos) / len(elos):+.1f} (n {len(elos)})" if elos else "no Elo"
            verdicts = dict(Counter(r["verdict"] for r in members).most_common())
            lines.append(f"- {name}: {len(members)} results, {verdicts}, {mean}")
        # Kept rates compare gain tests only: a formula idea removes a cutoff and is kept for no loss, not a gain.
        gains = {name: [r for r in groups[name] if (r.get("idea") or {}).get("kind") not in NONINFERIOR_KINDS]
                 for name in ("removed", "added")}
        if gains["removed"] and gains["added"]:
            rates = {name: (sum(r["verdict"] == "kept" for r in gains[name]), len(gains[name])) for name in gains}
            # chance.rates_p is one-sided: the group with the higher kept rate goes first.
            lead, trail = sorted(rates, key=lambda name: rates[name][0] / rates[name][1], reverse=True)
            p = chance.rates_p(*rates[lead], *rates[trail])
            lines.append(f"- kept, {lead} against {trail}: {rates[lead][0]}/{rates[lead][1]} against "
                         f"{rates[trail][0]}/{rates[trail][1]}; luck alone puts {lead} this far ahead p = {p:.2f}")
    if unread:
        lines.append(f"- n/a: {unread} results whose commits couldn't be read")
    lines.append(f"- the champion's count by version (kept results): {', '.join(counts) or 'none yet'}")
    return lines


def stats_markdown(results: Path = RESULTS, now: float | None = None) -> str:
    rows = latest_rows([json.loads(line) for line in results.read_text().splitlines() if line.strip()]
                       if results.exists() else [])
    ideas_path = results.parent / "ideas.jsonl"
    ideas = [json.loads(line) for line in ideas_path.read_text().splitlines() if line.strip()] if ideas_path.exists() else []
    rescreen_path = results.parent / "rescreen.json"
    rescreens = json.loads(rescreen_path.read_text()) if rescreen_path.exists() else {}
    by_category: dict[str, Counter] = defaultdict(Counter)
    reasons, verdicts = Counter(), Counter()
    import judge  # here: judge imports this module
    lines = ["# Experiments so far", "",
             "Each line says how many champions were promoted after it: a result from several promotions ago was",
             "measured against a different bot and may no longer hold.", ""]
    kept_after = [sum(r["verdict"] == "kept" for r in rows[i + 1:]) for i in range(len(rows))]
    for row, later in zip(rows, kept_after):
        # A noloss, formula or targeted result's SPRT asked "no worse?", not "a gain?": say so rather than a gain's
        # odds, and count its verdict apart from the gain tests'. A targeted one kept as a gain (driver.target_verdict:
        # its limit_kept's "of" "gain") was judged as a gain, so it says so and counts with them.
        as_gain = row["verdict"] == "kept" and ((row["sprt"].get("limit_kept") or {}).get("of") == "gain")
        noninferior = row["idea"].get("kind") in NONINFERIOR_KINDS and not as_gain
        by_category[row["idea"]["category"]][row["verdict"] + (" (no-loss test)" if noninferior else "")] += 1
        # A no-code result's reason is the coder's own words: counted as one reason, whichever it gave.
        reasons[("no code: no work" if no_work(row["session"]) else "no code: the coder's reason")
                if row["verdict"] == "no code" else row["reason"]] += 1
        elo = row["sprt"].get("elo")
        summary = (row["session"].get("summary") or "").replace("\n", " ")[:300]
        chance = None if noninferior else gain_probability(row["sprt"])
        odds = f", {chance:.0%} chance of a real gain" if chance is not None and row["verdict"] != "kept" else ""
        odds += (f" ({row['idea']['kind']}: tested for no loss, not for a gain)" if noninferior else
                 f" ({row['idea']['kind']}: kept as a gain)" if as_gain else "")
        if (target := row.get("target")) and target.get("line"):  # driver.target_test's paired sign test
            odds += f", target {target['line']}"
        stacked = f", stacked on {', '.join(row['stacked_on'])}" if row.get("stacked_on") else ""
        if fix := row["idea"].get("quick_fix"):  # driver.quick_fix_idea: its lineage's count, for the luck it adds
            stacked += f", quick fix {fix.get('lineage')} of its lineage on {fix.get('of')}"
        late = ""
        if row["verdict"] == "kept" and (done := rescreens.get(row.get("candidate") or "", {}).get("done")):
            late = (" Late screen vs the previous champion (entries added after its claim, loop/rescreen.py): "
                    + ", ".join(f"{o['name']} {o['candidate']:.2f} vs {o['base']:.2f} (z {o['z']:+.1f})"
                                + (" (worse)" if o.get("worse") else "") for o in done.values()) + ".")
        # Its independent judge's verdict (loop/judge.py; LOOP.md: Judged verdicts), from its run folder.
        if judged := judge.saved(results.parent, row["id"]):
            verdicts[(judged["asked"], judged["verdict"])] += 1
            late += f" Judged: {flat(judge.line(judged))[:300]}."
        if moves := (row.get("behaviour") or {}).get("moves"):  # loop/evalstats.py: candidate v champion, past chance
            late += f" Behaviour moved: {flat('; '.join(moves[:3]))[:400]}."
        lines.append(f"- {row['id']} [{row['idea']['id']} {row['idea']['category']}] {flat(row['idea']['title'])}: "
                     f"{row['verdict']} ({flat(row['reason'])}){stacked}, Elo {elo}{odds}{pairs_text(row)}, files {row['changed_files'][:4]}, "
                     f"{later} promotions since.{screen_text(row, results.parent)}{late}{review_text(row)} Lane said: "
                     f"{summary}")
    if verdicts:
        lines += ["", "Judged verdicts (asked -> applied, LOOP.md: Judged verdicts): " + ", ".join(
            f"{asked} -> {applied} {n}" for (asked, applied), n in sorted(verdicts.items())) + "."]
    blockers = [(r["id"], flat(m.group(1))) for r in rows[-30:]
                for m in [re.search(r"Blocked by:\s*(.+)", (r.get("session") or {}).get("summary") or "")]
                if m and m.group(1).strip().lower().rstrip(".") != "none"]
    lines += ["", "## What recent experiments were blocked by (their coders' own words)", ""]
    lines += [f"- {eid}: {cause[:200]}" for eid, cause in blockers] or ["- none reported"]
    lines += predictable_lines(rows, results.parent / "runs")
    stale = unrevisited(rows, ideas)
    lines += ["", f"## Not revisited: {len(stale)} results not kept, 3+ promotions old, that no later result or idea "
              "comes back to", ""]
    lines += [f"- {r['id']} [{r['idea']['id']}] {flat(r['idea']['title'])}: {r['verdict']}, Elo {r['sprt'].get('elo')}"
              for r in stale]
    lines += never_played_lines(rows, ideas, time.time() if now is None else now)
    lines += sibling_lines(ideas)
    if champion := next((r.get("candidate") for r in reversed(rows) if r["verdict"] == "kept"), None):
        import screenport
        scores = screenport.entry_scores(rows, rescreens, champion, results.parent)
        lines += ["", f"## Screen entries against the current champion {champion[:7]}", "",
                  "Each entry's points a game against the champion (0.50 = level): the champion's games on its "
                  "shared map set, else its latest screen, whichever has more games. An entry built on a "
                  f"champion {screenport.MIN_PROMOTIONS}+ promotions older scoring {screenport.PORT_AT}+ over "
                  f"{screenport.MIN_GAMES}+ games gets a port idea (loop/screenport.py).", ""]
        lines += [f"- {name} {s['score']:.2f} over {s['games']} games ({s['source']})"
                  for name, s in sorted(scores.items(), key=lambda item: -item[1]["score"])] or ["- none yet"]
    import advmaps  # the champion's record on maps/adversarial/ and its search's finds
    try:
        lines += advmaps.stats_lines(results.parent)
    except Exception as error:  # one report's fault never costs the rest of stats.md
        lines += ["", f"## Adversarial maps: the report failed ({type(error).__name__}: {error}); see loop/advmaps.py"]
    try:
        lines += cutoff_lines(rows)
    except Exception as error:  # one report's fault never costs the rest of stats.md
        lines += ["", f"## Hard cutoffs: the report failed ({type(error).__name__}: {error}); see loop/cutoffs.py"]
    import sweep
    lines += sweep.dead_lines(results.parent)
    lines += ["", "## Acceptance by category", ""]
    for category, counts in sorted(by_category.items()):
        lines.append(f"- {category}: {dict(counts)}")
    lines += ["", "## Outcome reasons", ""] + [f"- {reason}: {n}" for reason, n in reasons.most_common()]
    import throughput  # each recent result's wall time and games a minute per stage (MISSES 35)
    lines += throughput.stage_lines(rows, results.parent / "runs", time.time() if now is None else now)
    return "\n".join(lines) + "\n"


# A review's other edits to a waiting idea, which valid_backlog allowed: its prerequisites (`after`, remapped when a
# new idea it names was renumbered), the history notes it added, a map or opponent mix, and a drop with its reason. They were
# validated and then never applied (found 2026-09-26 23:5x: every review's orderings and notes had been lost).
def apply_edits(idea: dict, proposed: dict, before: dict, renamed: dict[str, str], review: str) -> None:
    if "after" in proposed and proposed.get("after") != before.get("after"):
        idea["after"] = [renamed.get(a, a) for a in proposed["after"]]
    notes = [review_note(renumbered(note, renamed), review)
             for note in (proposed.get("history") or [])[len(before.get("history") or []):]]
    history = idea.setdefault("history", [])
    history += [note for note in notes if note not in history]
    if proposed.get("evaluation") != before.get("evaluation"):
        idea["evaluation"] = proposed.get("evaluation")
    if proposed.get("status") == "dropped" and idea["status"] == "todo":
        idea["status"] = "dropped"
        history.append(f"review {review}: dropped")


# The changed ideas whose own version is what makes valid_backlog refuse `after_text` (put back as they were, or left
# out if new, the file passes), [] when none alone does (a duplicate or missing idea, or several faults at once), or
# None when the file isn't idea rows. backlog.apply refuses the edits on the ideas it names.
def backlog_fault_ids(before: list[dict], after_text: str) -> list[str] | None:
    try:
        after = [json.loads(line) for line in after_text.splitlines() if line.strip()]
        if not all(isinstance(idea, dict) and isinstance(idea.get("id"), str) for idea in after):
            return None
        earlier = {idea["id"]: idea for idea in before}
        return [idea["id"] for idea in after if earlier.get(idea["id"]) != idea and valid_backlog(before, "\n".join(
            json.dumps(earlier[other["id"]] if other is idea else other)
            for other in after if other is not idea or idea["id"] in earlier)) is not None]
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


# Why valid_backlog refused `after_text`, for the rejection line: the changed ideas backlog_fault_ids names, else the
# whole file (bad rows, a duplicate or missing idea, or several faults at once).
def backlog_faults(before: list[dict], after_text: str) -> str:
    try:
        rows = [json.loads(line) for line in after_text.splitlines() if line.strip()]
    except ValueError:
        return "ideas.jsonl is not valid idea rows"
    if not all(isinstance(idea, dict) and isinstance(idea.get("id"), str) for idea in rows):
        return "ideas.jsonl has a row that isn't an idea with an id"
    if (bad := backlog_fault_ids(before, after_text)) is None:
        return "ideas.jsonl is not valid idea rows"
    return f"ideas.jsonl breaks the backlog rules at {', '.join(bad)}" if bad else \
        "ideas.jsonl breaks the backlog rules as a whole (a duplicate or missing idea, or several faults)"


# `text` with each idea id a review gave a new idea of its own replaced by the id it was given (`renamed`, apply_review):
# the reviewer never saw an idea queued while it worked, so an id it writes that one took meanwhile is its own. In one
# pass, so an id is never renamed twice. The 21:12 review of 2026-09-27 wrote "after I484 (new)" on I187 and I363 for
# its census idea, given I492, while I484 had become the RL idea.
def renumbered(text: str, renamed: dict[str, str]) -> str:
    return re.sub(r"\bI\d+\b", lambda m: renamed.get(m.group(0), m.group(0)), text) if renamed else text


# A history note a review adds, marked as the review's so stats.md's "Not revisited" check (unrevisited) can tell it
# from the loop's own notes.
def review_note(note: str, review: str) -> str:
    return note if note.startswith("review ") else f"review {review}: {note}"


def valid_backlog(before: list[dict], after_text: str, params: str | None = None) -> list[dict] | None:
    """An old todo idea may change only its priority, rank (rank_ok, or dropped), `after`, map mix (evaluation.maps)
    and opponent mix (evaluation.opponents), and gain history notes, or be merged into another
    waiting idea (todo, or claimed meanwhile); every other old idea may only gain history notes. New ideas need usable,
    predeclared settings; a tune idea's constraints must name params.hpp numbers in `params` (default: the
    champion's)."""
    try:
        after = [json.loads(line) for line in after_text.splitlines() if line.strip()]
    except json.JSONDecodeError:
        return None
    if not all(isinstance(idea, dict) for idea in after):
        return None
    if not all(isinstance(idea.get("id"), str) for idea in after):
        return None
    by_id = {idea["id"]: idea for idea in after}
    if len(by_id) != len(after):
        return None
    for old in before:
        new = by_id.get(old["id"])
        allowed = {"priority"} if old["status"] == "todo" else set()
        # A todo idea may gain, change or drop a rank, its place within its priority (driver.claim_order, M70); a
        # null rank drops it, as apply_review reads it.
        if new is not None and old["status"] == "todo" and ("rank" in new or "rank" in old):
            if new.get("rank") is not None and not rank_ok(new["rank"]):
                return None
            allowed.add("rank")
        # A todo idea may be ordered after others it needs ("after": idea ids).
        if new is not None and old["status"] == "todo" and "after" in new:
            if not (isinstance(new["after"], list) and all(isinstance(a, str) and a in by_id for a in new["after"])):
                return None
            allowed.add("after")
        # Any idea's history may gain notes (a review's new measurements, or why a result's verdict still holds, which
        # the prompt's "Not revisited" check asks for on finished ideas: 3 reviews of 2026-09-27 were rejected for
        # it), never lose or reword one.
        if new is not None and "history" in new:
            history, kept = new["history"], old.get("history") or []
            if not (isinstance(history, list) and history[:len(kept)] == kept
                    and all(isinstance(note, str) and note.strip() for note in history[len(kept):])):
                return None
            allowed.add("history")
        # A todo idea may gain, change or drop a map mix (evaluation.maps) or an opponent mix (evaluation.opponents)
        # before it is claimed, since settings freeze at claim (the 23:49 review of 2026-09-26 gave I306 big maps and
        # was rejected for it); nothing else in its evaluation may change.
        if new is not None and old["status"] == "todo" and new.get("evaluation") != old.get("evaluation"):
            was, now = dict(old.get("evaluation") or {}), dict(new.get("evaluation") or {})
            mix, opponents = now.pop("maps", None), now.pop("opponents", None)
            was.pop("maps", None)
            was.pop("opponents", None)
            if not was:  # an idea with no settings plays its kind's defaults: only those may be written out
                was = {"elo0": NOLOSS_ELO0, "elo1": NOLOSS_ELO1} if old.get("kind") in NONINFERIOR_KINDS else \
                    {"elo0": 0, "elo1": 15}
                was.update(max_pairs=2400, fresh=True)
                now = {**was, **now} if set(now) <= set(was) else now
            if was != now or (mix is not None and not map_mix_ok(mix, new.get("kind"))) or (
                    opponents is not None and not opponent_mix_ok(opponents, new.get("kind"))):
                return None
            allowed.add("evaluation")
        # A todo idea may be dropped with a reason: a history note added in this review (I007 and I036, 23:49).
        if new is not None and old["status"] == "todo" and new.get("status") == "dropped":
            if len(new.get("history") or []) <= len(old.get("history") or []):
                return None
            allowed.add("status")
        if new is not None and old["status"] == "todo" and new.get("status") == "merged":
            target = by_id.get(new.get("merged_into"))
            # A target claimed while the review ran is still the same change (I165 into I193, being tried as E0098).
            if target is None or target is new or not (target.get("status") == "todo"
                                                       or str(target.get("status", "")).startswith("trying:")):
                return None
            allowed |= {"priority", "status", "merged_into"}  # keeping the after/history notes allowed above
        if new is None or {k: v for k, v in new.items() if k not in allowed} != {
                k: v for k, v in old.items() if k not in allowed}:
            return None
    known = {idea["id"] for idea in before}
    for idea in after:
        if not idea_fields_ok(idea):
            return None
        if idea["id"] in known and idea["status"] != "todo":
            continue  # already checked above: it may only have gained history notes
        if idea["id"] not in known and new_idea_fault(idea, params) is not None:
            return None
    return after


# Why new idea `idea` (not yet in the backlog) can't be queued, naming the field, or None: valid_backlog's checks of a
# new idea's settings, which proposal_fault reports. `params` is the text a tune's constraints are checked against
# (default: the champion's params.hpp).
def new_idea_fault(idea: dict, params: str | None = None) -> str | None:
    if idea["status"] != "todo" or not re.fullmatch(r"I\d+", idea["id"]):
        return "a new idea must be todo with an I<number> id"
    kind, tune = idea.get("kind"), idea.get("tune")
    if kind is not None and kind not in KINDS:
        return f"its kind {kind!r} is not one of {', '.join(KINDS)}"
    if "constraints" in idea:  # they belong inside "tune"; at the top level the tuner would never see them
        return "its constraints go inside tune"
    if (kind == "targeted") != ("target" in idea) or ("target" in idea and not target_ok(idea["target"])):
        return "a targeted idea, and only one, needs a target {measure (referee/measures.py), direction}"
    if idea.get("rank") is not None and not rank_ok(idea["rank"]):
        return "its rank is not a finite number"
    if kind == "tune" and not (isinstance(tune, dict) and isinstance(tune.get("params"), list)
                               and tune["params"] and all(isinstance(p, str) for p in tune["params"])):
        return "a tune idea needs tune.params, a list of params.hpp names"
    if kind == "tune" and "constraints" in tune:
        params = champion_params() if params is None else params
        if not isinstance(tune["constraints"], list) or not tune_orders_ok(tune, params):
            return "its tune.constraints must order params.hpp numbers it tunes (tune_orders_ok)"
    policy = idea.get("evaluation") or {}  # null is no settings: apply_review drops it
    settings = {"elo0", "elo1", "alpha", "beta", "max_pairs", "fresh", "pool", "pool_pairs", "maps", "opponents"}
    if not isinstance(policy, dict) or set(policy) - settings:
        return f"its evaluation may hold only {', '.join(sorted(settings))}"
    if "maps" in policy and not (map_mix_ok(policy["maps"], kind) and policy.get("fresh", True)):
        return (f"its evaluation.maps is not a map mix (map_mix_ok: known styles, each aimed share {MAP_SHARE[0]:g}-"
                f"{MAP_SHARE[1]:g} and {MAP_SHARE[1]:g} together, fresh maps, none for speed or identical)")
    if "opponents" in policy and not opponent_mix_ok(policy["opponents"], kind):
        return (f"its evaluation.opponents must name one opponent of meta.MIX_OPPONENTS with a share "
                f"{MAP_SHARE[0]:g}-{MAP_SHARE[1]:g} (none for speed, identical or tune)")
    for key, value in policy.items():
        if key in ("maps", "opponents"):
            continue
        if key in ("fresh", "pool"):
            if type(value) is not bool:
                return f"its evaluation.{key} is not true or false"
        elif type(value) not in (int, float) or not math.isfinite(value):
            return f"its evaluation.{key} is not a finite number"
        elif key in ("elo0", "elo1") and kind in NONINFERIOR_KINDS:
            continue  # checked together below
        elif key == "elo0":
            if value != 0:
                return "its evaluation.elo0 must be 0 (a gain test)"
        elif value <= 0 or (key in ("alpha", "beta") and value > 0.05):
            return f"its evaluation.{key} must be above 0" + (" and at most 0.05" if key in ("alpha", "beta") else "")
        elif key in ("max_pairs", "pool_pairs") and type(value) is not int:
            return f"its evaluation.{key} is not a whole number"
    if kind in NONINFERIOR_KINDS and not noloss_bounds_ok(policy.get("elo0", NOLOSS_ELO0),
                                                          policy.get("elo1", NOLOSS_ELO1)):
        return f"a {kind} idea's bounds must be elo1 {NOLOSS_ELO1} and elo0 from {NOLOSS_ELO0} to below 0"
    return None


IDEA_FIELDS = ("id", "category", "priority", "title", "hypothesis", "status")


# Whether an ideas.jsonl row has the fields every reader relies on (a row without "status", I340 on 2026-09-27,
# crashed every driver): all of IDEA_FIELDS, with "id" and "status" non-empty strings; a todo idea also needs a
# priority 1-5 and the other fields non-empty strings. Older finished ideas may predate those rules (I020's priority 0).
# An idea's `rank`, its place within its priority (driver.claim_order: lower first): a finite number, not a bool.
def rank_ok(rank: object) -> bool:
    return type(rank) in (int, float) and math.isfinite(rank)


# sig's latest ranking of the queue, so a reorder that overrides it shows (REFLECT 2026-09-30 15:13 F2, MISSES 66: at
# 12:33 the orchestrator had moved I1108, I1118 and I1119 ahead of the 11:10 expected-Elo re-rank sig asked for,
# saying so only when he asked). <loop home>/ranking.json is {"at", "source" (sig's words and date), "order" (idea ids,
# first to claim first)}, written by `driver.py --ranking` whenever a ranking sig asked for is applied, and replaced
# whole by the next (a few KB). out_of_rank names each idea of `sequence` (the ready ideas in claim order) placed
# ahead of a ranked one it shouldn't be (unranked, or ranked lower) with no orchestrator note since the ranking saying
# why (noted_since). `driver.py --out-of-rank` places one there on purpose, with that note and the decisions.md line
# naming the ranking it overrides and why (driver.place_out_of_rank).
RANKING = "ranking.json"


def read_ranking(state: Path) -> dict:
    try:
        ranking = json.loads((state / RANKING).read_text())
    except (OSError, ValueError):
        return {}
    return ranking if isinstance(ranking, dict) and isinstance(ranking.get("order"), list) else {}


def out_of_rank(sequence: list[dict], ranking: dict) -> list[tuple[str, str]]:
    order = {idea: k for k, idea in enumerate(ranking.get("order") or []) if isinstance(idea, str)}
    ids = [idea["id"] for idea in sequence]
    said = {idea["id"] for idea in sequence if noted_since(idea, ranking.get("at"))}
    found = []
    for k, idea in enumerate(ids):
        if idea in said:
            continue
        if ahead := next((later for later in ids[k + 1:] if later in order
                          and (idea not in order or order[idea] > order[later])), None):
            found.append((idea, ahead))
    return found


# Whether idea `idea` has an orchestrator note (driver.orchestrator_note's "orchestrator YYYY-MM-DD HH:MM: ", not
# its automatic ones, judge.AUTOMATIC) stamped in or after the minute `at`: the orchestrator said why it stands where
# it does, as `driver.py --out-of-rank` does for each idea it places (driver.place_out_of_rank).
NOTE_STAMP = re.compile(r"orchestrator (\d{4}-\d{2}-\d{2} \d{2}:\d{2}): ")


def noted_since(idea: dict, at: object) -> bool:
    import judge  # lazily: judge imports this module
    if not isinstance(at, (int, float)):
        return False
    since = time.strftime("%Y-%m-%d %H:%M", time.localtime(at))
    return any(isinstance(note, str) and (stamp := NOTE_STAMP.match(note)) and stamp.group(1) >= since
               and not note[stamp.end():].startswith(judge.AUTOMATIC) for note in idea.get("history") or [])


def idea_fields_ok(idea: object) -> bool:
    def text(key: str) -> bool:
        return isinstance(idea[key], str) and bool(idea[key].strip())
    if not isinstance(idea, dict) or not set(IDEA_FIELDS) <= idea.keys() or not (text("id") and text("status")):
        return False
    return idea["status"] != "todo" or (type(idea["priority"]) is int and 1 <= idea["priority"] <= 5
                                        and all(text(k) for k in IDEA_FIELDS if k != "priority"))


# ideas.jsonl's text for `ideas`, for writing it: raises ValueError, naming the rows, when any fails idea_fields_ok, so a
# bad row is never written, and logs the write (log_ideas_write). Every writer of the live backlog goes through this,
# and only writers call it: a caller that only previews the text would log a write that never happened.
def ideas_text(ideas: list[dict]) -> str:
    if bad := [idea for idea in ideas if not idea_fields_ok(idea)]:
        names = ", ".join(str(idea.get("id") if isinstance(idea, dict) else idea)[:40] for idea in bad[:5])
        raise ValueError(f"refusing to write ideas.jsonl: {len(bad)} rows lack a required field of {IDEA_FIELDS} "
                         f"({names}); add ideas with meta.append_ideas")
    log_ideas_write(ideas)
    return "".join(json.dumps(idea) + "\n" for idea in ideas)


# A line in <loop home>/ideas-writes.log for each backlog write: time, process, its command, the four calling frames
# and the ideas then held by a lane, so a write that restores an older copy (MISSES 89: finish_idea's notes lost for
# E0303, E0304, E0315, E0316 and E0322 with no writer found) names its process. Kept under 2 MB (the older half goes).
def log_ideas_write(ideas: list[dict]) -> None:
    if "unittest" in sys.modules:  # a test's backlog is never the live one
        return
    try:
        import traceback
        frames = traceback.extract_stack()[-6:-2]
        trying = sorted(f"{i['id']}:{i['status'][len('trying:'):]}" for i in ideas if str(i.get("status")).startswith("trying:"))
        line = json.dumps({"at": round(time.time(), 3), "pid": os.getpid(), "ppid": os.getppid(),
                           "argv": sys.argv[:4], "frames": [f"{Path(f.filename).name}:{f.lineno} {f.name}" for f in frames],
                           "n": len(ideas), "trying": trying})
        path = loop_home() / "ideas-writes.log"
        if path.exists() and path.stat().st_size > 2_000_000:
            path.write_text("".join(path.read_text().splitlines(keepends=True)[-2000:]))
        with path.open("a") as handle:
            handle.write(line + "\n")
    except Exception:  # a diagnostic never blocks a write
        pass


# An idea that forbids a behaviour by pointing at a past result ("Do not add X; that lost twice (E0026)").
# One loss rejects one implementation on one champion, so ideas cite results as evidence, never as bans.
# A ban is an instruction, so the negation starts a sentence or clause; mid-clause it describes something ("a
# never-folding swarm (E0053)", I264; "dragons that don't decide the tiebreak (E0075, ...)", a review, 2026-09-26).
BAN = re.compile(r"(?:^|[.;:!?(]\s*|\n\s*)(do not|don't|never)\b(?!-)[^.]{0,160}?\b(lost|lose|failed|hurt|E\d{4})\b",
                 re.I)


# A mechanism that keys on which map this is (MISSES 102; sig, 30 Sep: "lets not hardcode to specific maps pls cos the
# actual sprint uses unseen maps"): map files embedded in the bot, recognising a stored (known, public, ladder,
# official) map, detecting or hardcoding a named ladder map, a map book, or switches set per map. refusal asks it of
# every new idea (valid_proposals, a review's), and driver.mark_idea of an idea raised to priority 1 or next, where
# I200's miss happened; it pays only on maps we know. A map book cited as an oracle (I1043, I1057: E0372's map-book
# oracle as evidence for general rules) or "the map-book idea" (I201) doesn't count, nor does a per-map style, size or
# count, or a map seed, or a value keyed on what the bot observes of a map (its area or style: loop/tunesplit.py's
# formula ideas, whose Options line says "so no per-map value is needed").
_LADDER = r"(?:Schooltime|Trauma|Autarky|Dilemma|Trophy|Devil|Stronghold|Queen\s+Of\s+Spades|Slithery\s+Fight)"
_NOT_A_MAP = r"(?!\s+(?:style|family|size|type|group|kind|seed|counts?|means?|layouts?)\b)(?!['’]s)"
MAP_IDENTITY = re.compile(
    r"\b(?:embed\w*|#embed|stor(?:e|es|ed|ing)|includ\w+|bak\w+)\s+(?:the\s+|our\s+|its\s+)?(?:\w+\s+){0,2}?"
    r"(?:ladder|official|public|known|tournament|stored)\s+maps?\b|\b(?:embed\w*|#embed|includ\w+)\s+(?:the\s+)?"
    r"(?:\w+\s+){0,2}?map\s+files?\b"
    rf"|\brecogni[sz]\w*\s+(?:\w+\s+){{0,4}}?(?:stored|known|public|ladder|official|embedded)\s+maps?\b"
    rf"|\b(?:recogni[sz]\w*|identif\w*|detect\w*|look\w*\s+up)\s+(?:which|the)\s+map\b{_NOT_A_MAP}"
    rf"|\b(?:recogni[sz]\w*|identif\w*|detect\w*|hardcod\w*|hard-cod\w*)\s+(?:\w+\s+){{0,2}}?{_LADDER}\b"
    r"|\bmap[- ]book\b(?![- ](?:oracle|idea))(?!\s+(?:used\s+)?as\s+an?\s+oracle)"
    r"|(?<!\bno )(?<!\bnot a )(?<!\bwithout )(?<!\bwithout a )"
    r"\bper[- ]map\s+(?:switch|toggle|flag|setting|constant|value)"
    rf"|\b(?:switch|turn)\w*\s+(?:\w+\s+){{0,3}}?(?:on|off)\s+(?:per|by|for\s+each)\s+map\b{_NOT_A_MAP}"
    rf"|\bset\w*\s+(?:P\.)?\w+\s+per\s+map\b{_NOT_A_MAP}",
    re.I)


def bans_by_result(idea: dict) -> bool:
    return bool(BAN.search(f"{idea.get('title', '')}. {idea.get('hypothesis', '')}"))


# Whether a tune idea's "constraints" parse against `params` (params.hpp text) and can all hold from its values
# there within the tuner's clamps, as tune.py checks before its first game (parse_orders, feasible_bounds).
def tune_orders_ok(tune_spec: dict, params: str) -> bool:
    import tune
    numbers = tune.read_params(params)
    tuned = [p for p in tune_spec["params"] if p in numbers]  # the tuner skips names params.hpp lacks
    try:
        orders = tune.parse_orders(tune_spec["constraints"], tune.param_kinds(params), tuned)
        names = {n for o in orders for n in (o.lo, o.hi)}
        tune.feasible_bounds(orders, {n: tune.clamp_range(*numbers[n]) if n in tuned else (numbers[n][1],) * 2
                                      for n in names}, {n: numbers[n][0] for n in names})
    except ValueError:
        return False
    return True


# The champion's bot/params.hpp, to check tune constraints' names against ("" when it can't be read).
def champion_params(repo: Path = Path(__file__).resolve().parent.parent) -> str:
    result = subprocess.run(["git", "show", "loop/champion:bot/params.hpp"], cwd=repo, text=True, capture_output=True)
    return result.stdout if result.returncode == 0 else ""


def champion_commit(repo: Path) -> str:
    result = subprocess.run(["git", "rev-parse", "loop/champion"], cwd=repo, text=True, capture_output=True)
    return result.stdout.strip() if result.returncode == 0 else ""


DIGESTS_CHARS = 150_000  # the newest digests a review gets: about 15 of the 4-hourly ones (about 10 KB each, 26-29
# Sep); the daily ones since are larger, and a review's period (META_EVERY_S) spans two
LATEST_CHARS = 60_000  # of the latest read's latest.md (a day's is 150-360 KB), cut by latest_for_review
# latest.md's sections a review keeps first, before the chatty rest: the organisers' channels and the forums, where
# they answer.
LATEST_FIRST = ("announcements", "changelog", "ask-devs", "community-maps", "bug-reports")


# latest.md (tools/discord_read.py: a header, then one `## #channel (N)` section each, message text escaped so none can
# forge one) in about `cap` characters: the header, LATEST_FIRST's sections, then the others in their order, each whole
# while it fits; one that doesn't keeps its heading and newest lines in what is left, and the rest are only named,
# with a pointer to `path` for the whole read.
def latest_for_review(text: str, cap: int, path: Path) -> str:
    head, *sections = re.split(r"(?m)^(?=## #)", text)
    first = [s for s in sections if s.split(None, 2)[1].removeprefix("#") in LATEST_FIRST]
    kept, left, cut = [head[:cap]], cap - len(head[:cap]), []
    for section in first + [s for s in sections if s not in first]:
        if len(section) <= left:
            kept.append(section)
            left -= len(section)
            continue
        heading, _, body = section.partition("\n")
        note = f"{heading}\n(older messages cut; the whole section is in {path})\n"
        lines, size = body.splitlines(keepends=True), len(note)
        start = len(lines)
        while start and size + len(lines[start - 1]) <= left:
            start -= 1
            size += len(lines[start])
        if start < len(lines):
            kept.append(note + "".join(lines[start:]))
            left = 0
        else:
            cut.append(heading.removeprefix("## "))
    if cut:
        kept.append(f"\n(not shown, past {cap:,} characters: {', '.join(cut)}; see {path})\n")
    return "".join(kept)


# What a review reads of the Discord: every section of the orchestrator's own decisions and corrections in
# discord/digests.md, in full (short, and what the digests came to), then the newest digests that fit in
# DIGESTS_CHARS (with a pointer to the file for the rest), then the latest read's own messages, whose window the
# digests may not have covered yet.
def discord_notes(state: Path) -> str:
    digests, latest = state / "discord" / "digests.md", state / "discord" / "latest.md"
    text = digests.read_text() if digests.exists() else ""
    sections = re.split(r"(?m)^(?=## )", text)
    # Every heading saying "decision" ("## 2026-09-27 15:40 decisions ...", "## Decisions (...)"), except a digest's
    # own numbered item ("## D3. Decisions the organisers ...").
    decided = [s for s in sections if s.startswith("## Correction") or (
        "decision" in (heading := s.split("\n", 1)[0].lower()) and not re.match(r"## [a-z]\d+\. ", heading))]
    reports = [s for s in sections if s.startswith("## ") and s not in decided]
    kept, size = [], 0
    for section in reversed(reports):
        if size + len(section) > DIGESTS_CHARS:
            break
        kept.insert(0, section)
        size += len(section)
    older = (f"(older digests, including the first full-history ones, are in {digests})\n\n"
             if len(kept) < len(reports) else "")
    raw = latest_for_review(latest.read_text(), LATEST_CHARS, latest) if latest.exists() else "(not read yet)\n"
    return ("# Decisions so far\n\n" + ("".join(decided) or "(none yet)\n") + "\n# Digests\n\n"
            + older + ("".join(kept) or "(no digests yet)\n") + f"\n\n# Latest read (raw messages)\n\n{raw}")


def prepare_review(repo: Path, state: Path) -> Path | None:
    """The folder of the review to run, or None: an already-pending review's (review.json "pending"), else a new one
    persisted META_EVERY_S after the last review finished (review.json "finished_at"; at once with none), or when
    fewer than LOW_QUEUE ideas are to do and it finished at least 3600 s ago. Both providers get the same task."""
    import online
    with locked(state / "review"), locked(state / "ideas.jsonl"):
        record = online.read(state / "review.json", {})
        if record.get("pending"):
            return Path(record["pending"])
        results = state / "results.jsonl"
        done = len([line for line in results.read_text().splitlines() if line.strip()]) if results.exists() else 0
        ideas = [json.loads(line) for line in (state / "ideas.jsonl").read_text().splitlines() if line.strip()]
        low = sum(i["status"] == "todo" for i in ideas) < LOW_QUEUE
        since = time.time() - record.get("finished_at", 0)
        if since < META_EVERY_S and (not low or since < 3600):
            return None
        champion = champion_commit(repo)  # recorded with the review: which bot it re-ranked the backlog against
        work = state / "meta" / str(time.time_ns())
        work.mkdir(parents=True)
        text = "".join(json.dumps(i) + "\n" for i in ideas)
        (work / "before.jsonl").write_text(text)
        (work / "ideas.jsonl").write_text(text)
        method = state / "method.md"
        (work / "method.md").write_text(method.read_text() if method.exists() else "# Open findings about the tests\n")
        shutil.copy(work / "method.md", work / "method.before.md")  # to tell, when applying, if it moved meanwhile
        for name in ("playbook.md", "evidence.md", "scout.md", "study.md", "behaviour-gaps.md", "briefing.md"):
            source = state / name
            (work / name).write_text(source.read_text() if source.exists() else "(no evidence yet)\n")
        (work / "discord.md").write_text(discord_notes(state))
        shutil.copy(repo / "loop/MISSES.md", work / "misses.md")
        (work / "stats.md").write_text(stats_markdown(results))
        try:  # a report: bceval failing never stops the review (the online cycle alerts its errors)
            turns = online.bceval_text(state)
        except Exception:
            turns = ""
        (work / "online.md").write_text(online.evidence_text(state) + online.doom_text(state) + turns)
        (work / "context.md").write_text(f"Champion implementation: {state / 'champion/bot'}\n"
            f"Research: {repo / 'research'}\nCurrent official docs: {repo / 'docs/unsw'}\n"
            f"Experiment evidence and retained diffs: {state / 'runs'}\n"
            f"Top-team replays: {state / 'study-replays'}; read one with "
            f"`{repo / '.venv/bin/python'} {repo / 'tools/replays.py'} summary|deaths|lengths FILE`; watch one with "
            f"`{repo / '.venv/bin/python'} {repo / 'tools/watch.py'} scan|dragon|frames|doom` (see its --help)\n"
            f"This review's fresh games of the champion: {work / 'rule-counts.md'} (its canonical copy and the kept "
            f"replays are in {state / 'rule-games' / work.name})\n"
            f"Replay audit findings (what our games actually show): {state / 'audits/latest.md'}; "
            f"each experiment's lost test games: {state / 'runs'}/E####/losses\n"
            "Use these read-only paths; never edit the source backlog, referee, credentials, or champion.\n")
        reviews = len(list((state / "meta").iterdir()))
        area, lens = review_focus(reviews)
        import driver
        import rulecounts
        sizes = ", ".join(f"{count} of {band} tiles" for band, count in rulecounts.SIZES.items())
        styles = ", ".join(f"{count} {style}" for style, count in rulecounts.STYLES.items())
        prompt = (with_idea_rule(PROMPT).format(max_lines=MAX_PLAYBOOK_LINES, max_method=MAX_METHOD_LINES,
                                                audit_area=area, lens=lens, sizes=sizes, styles=styles,
                                                target_p=TARGET_P,
                                                measures=measure_list(), mix_opponents=mix_opponent_list(),
                                                options_pass=OPTIONS_PASS)
                  + "\n\nCurrent shared policy:\n" + (repo / "AGENTS.md").read_text()
                  + "\n\nHow the loop works (loop/LOOP.md, the policy's reference):\n"
                  + (repo / "loop/LOOP.md").read_text())
        (work / "PROMPT.md").write_text(prompt)
        rulecounts.save_prompt(state, work, prompt)  # the copy the worker is started from, out of its reach
        online.save(state / "review.json", {**record, "pending": str(work), "started_completed": done,
                                           "started_champion": champion})
        return work


WORKER_FILE_MAX = 16 * 1024 * 1024  # a worker's ideas.jsonl, notes or session.json never approach this


# A file a confined worker (a review, an audit, map ideas) or a coder could have replaced, read without following it:
# only a regular file with one link, opened with O_NOFOLLOW, so a symlink or hard link to a secret
# (~/.unswbc/keys.json) is refused rather than copied into live state by this unsandboxed step. Raises OSError
# otherwise.
def worker_file(path: Path) -> str:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, encoding="utf-8") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise OSError(f"{path} is not a plain file")
        if info.st_size > WORKER_FILE_MAX:  # a sparse 50 GB file costs nothing on disk and everything in memory
            raise OSError(f"{path} is {info.st_size} bytes, over {WORKER_FILE_MAX}")
        return handle.read(WORKER_FILE_MAX + 1)


def apply_review(state: Path, work: Path) -> str:
    """Validate private edits and merge without erasing outcomes written while the reviewer worked."""
    import online
    with locked(state / "review"), locked(state / "ideas.jsonl"):
        record = online.read(state / "review.json", {})
        if not record.get("pending") or work.resolve() != Path(record["pending"]).resolve():
            raise ValueError("not the pending review")
        before = [json.loads(line) for line in worker_file(work / "before.jsonl").splitlines() if line.strip()]
        backlog = valid_backlog(before, worker_file(work / "ideas.jsonl"))
        playbook = worker_file(work / "playbook.md")
        method = worker_file(work / "method.md") if (work / "method.md").exists() else None
        if backlog is None or len(playbook.splitlines()) > MAX_PLAYBOOK_LINES or (
                method is not None and len(method.splitlines()) > MAX_METHOD_LINES):
            faults = [f"playbook.md {len(playbook.splitlines())} lines, over {MAX_PLAYBOOK_LINES}"] \
                if len(playbook.splitlines()) > MAX_PLAYBOOK_LINES else []
            if method is not None and len(method.splitlines()) > MAX_METHOD_LINES:
                faults.append(f"method.md {len(method.splitlines())} lines, over {MAX_METHOD_LINES}")
            if backlog is None:
                faults.append(backlog_faults(before, worker_file(work / "ideas.jsonl")))
            return f"meta rejected: {'; '.join(faults)} ({work}); correct the pending files"
        live = [json.loads(line) for line in (state / "ideas.jsonl").read_text().splitlines() if line.strip()]
        known = {idea["id"] for idea in live}
        old = {idea["id"] for idea in before}
        # A review's new idea never carries "next" (claimed before everything): only the orchestrator (and a code screen
        # port, screenport.code_port) sets it. Its history notes are all the review's (review_note), so none can pass as
        # the orchestrator's to a coder (driver.note_source); a history that isn't a list is dropped. Its title and
        # category come out on one line (one_line).
        # An "evaluation": null (the 21:53 review of 2026-09-29 wrote one on its I1018, rejected whole) is no settings.
        added = [one_line({**{k: v for k, v in idea.items() if k not in ("next", "history")
                              and not (k == "evaluation" and v is None)}, "source": "meta",
                           **({"history": [review_note(str(n), work.name) for n in idea["history"]]}
                              if isinstance(idea.get("history"), list) else {})})
                 for idea in backlog if idea["id"] not in old]
        # A new idea valid_proposals would refuse (refusal: a ban by result, a bare threshold, no Options line) is left
        # out, not the whole review (two reviews waited an hour each on one sentence the check misread, 2026-09-26):
        # the alert quotes why, so the orchestrator can judge it. 85 of 284 todo ideas came from reviews on 2026-09-28.
        refused = {idea["id"]: why for idea in added if (why := refusal(idea, ideas=live + added))}
        if refused:  # report_refused alerts each and saves it in the review's protected run folder
            import rulecounts
            report_refused(state, rulecounts.run_folder(state, work), f"review {work.name}",
                           [(json.dumps(idea), f"{idea['id']} {refused[idea['id']]}") for idea in added
                            if idea["id"] in refused])
        added = [idea for idea in added if idea["id"] not in refused]
        # Ideas queued while the reviewer worked (post-mortem follow-ups) may already use its new ids.
        number = max((int(i["id"][1:]) for i in live + added if re.fullmatch(r"I\d+", i["id"])), default=0)
        renamed = {}
        for idea in added:
            if idea["id"] in known:
                number += 1
                renamed[idea["id"]] = f"I{number:03d}"
                idea["id"] = renamed[idea["id"]]
        for idea in added:  # its text naming the review's own new ideas, "same change as" included (merge_twin)
            idea.update({k: renumbered(idea[k], renamed) for k in ("title", "hypothesis") if isinstance(idea.get(k), str)})
            if isinstance(idea.get("history"), list):
                idea["history"] = [renumbered(note, renamed) for note in idea["history"]]
        proposed = {idea["id"]: idea for idea in backlog}
        # A priority someone changed while the review ran (the orchestrator, at sig's request) is newer than the
        # review's copy, which never saw it: keep it (2026-09-26: I004's and I210's raise to 1 was reverted that way).
        snapshot = {idea["id"]: idea.get("priority") for idea in before}
        ranks = {idea["id"]: idea.get("rank") for idea in before}
        earlier = {idea["id"]: idea for idea in before}
        still_todo = {i["id"] for i in live if i["status"] == "todo"} | {i["id"] for i in added}
        # What each existing idea looked like, so its edits, merge or drop are listed for a decision (MISSES 52).
        was = {i["id"]: {k: json.dumps(i.get(k)) for k in EDITED} for i in live}
        merges = []
        for idea in live:
            if idea["status"] == "todo" and idea["id"] in old:  # not a follow-up queued meanwhile under a new id
                # sig, 2026-09-27: "everyhting currnelty in p1 in the queue should rmeain p1 tho. and amek sure all the
                # formula-related adjustment ones are all high priority too": a priority-1 idea pinned then ("pin") or
                # of kind formula stayed at 1 (the 02:47 review of 2026-09-28 lowered 33, MISSES 66); since reviews can
                # order ideas within a priority by "rank" (method M70), no review lowers a priority-1 idea.
                if idea.get("priority") == snapshot.get(idea["id"]) and snapshot.get(idea["id"]) != 1:
                    idea["priority"] = proposed[idea["id"]]["priority"]
                # A rank someone changed while the review ran is newer than the review's copy, as a priority is.
                if idea.get("rank") == ranks.get(idea["id"]) and proposed[idea["id"]].get("rank") != idea.get("rank"):
                    if (rank := proposed[idea["id"]].get("rank")) is None:
                        idea.pop("rank", None)
                    else:
                        idea["rank"] = rank
                # A merge holds only while its target is still waiting (a lane may have claimed it meanwhile).
                target = proposed[idea["id"]].get("merged_into")
                if target not in old:  # a new idea from this review, which may have been renumbered
                    target = renamed.get(target, target)
                if proposed[idea["id"]].get("status") == "merged" and target in still_todo:
                    idea.update(status="merged", merged_into=target)
                    idea.setdefault("history", []).append(f"review {work.name}: merged into {target}")
                    merges.append(idea)
                apply_edits(idea, proposed[idea["id"]], earlier[idea["id"]], renamed, work.name)
            elif idea["id"] in old:  # a finished or claimed idea takes only the review's new history notes
                notes = [review_note(renumbered(note, renamed), work.name) for note in
                         (proposed[idea["id"]].get("history") or [])[len(earlier[idea["id"]].get("history") or []):]]
                history = idea.setdefault("history", [])
                history += [note for note in notes if note not in history]
        for idea in added:  # a new idea after another new one that was renumbered
            if idea.get("after"):
                idea["after"] = [renamed.get(a, a) for a in idea["after"]]
        rows = {i["id"]: i for i in live + added}
        for idea in merges:  # after every edit, so the review's own priority for the kept idea can't undo it
            absorb(rows[idea["merged_into"]], idea, rows)
        # A new idea the review calls the "same change as" a waiting one, or gives its headline, merges into it as
        # append_ideas' do (M42: six debug-log-field ideas each paid a full screen).
        for idea in added:
            merge_twin(idea, live, added)
        # Its new ideas are checked against the backlog as they join, as append_ideas' are (intake_check); the review
        # read only the Split findings stats.md listed before it wrote them.
        flagged = intake_check(live, added, f" from review {work.name}")
        temporary = state / "ideas.jsonl.tmp"
        temporary.write_text(ideas_text(live + added))
        temporary.replace(state / "ideas.jsonl")
        intake_alert(state, flagged)
        (state / "playbook.md").write_text(playbook)
        previous = (state / "method.md").read_text() if (state / "method.md").exists() else None
        changed = method is not None and method.strip() != (previous or "# Open findings about the tests").strip()
        before = work / "method.before.md"
        # The orchestrator may have edited the notes while this review ran (e.g. marking a finding done): keep that
        # and hand it the review's version to merge, rather than silently overwriting its edits.
        edited = before.exists() and previous is not None and previous.strip() != worker_file(before).strip()
        notes = ""
        if changed and edited:
            (state / "method.review.md").write_text(method)
            notes = (f"; METHOD NOTES CHANGED while they were edited: merge {state / 'method.review.md'} into "
                     f"{state / 'method.md'} and raise them with sig")
        elif changed:
            (state / "method.md").write_text(method)
            notes = f"; METHOD NOTES CHANGED: read {state / 'method.md'} and raise them with sig"
        online.save(state / "review.json", {"completed": record["started_completed"], "finished_at": time.time(),
                                           "champion": record.get("started_champion"), "last": str(work)})
        # After review.json, so a listing that fails can't make a retry add the same ideas again under new ids.
        import decisions
        source = f"review:{work.name}"
        try:
            if report := decisions.applied(state, source, added,
                                           decisions.changed_notes(previous or "", method) if changed else {},
                                           review_changes(live, was)):
                notes += f"; decide on each item of {report} (loop/decisions.py)"
        except OSError as error:
            alert(state, f"could not list applied ideas for {source}: {error}")
            notes += f"; could not list them for a decision ({error}): decide on each by hand"
        left = f"; left out {sorted(refused)} (refused: loop/GENERALISE.md)" if refused else ""
        return f"meta applied: {len(added)} new ideas{left}{notes} ({work})"


EDITED = ("priority", "rank", "after", "evaluation", "status")  # the fields a review may change on an existing idea


# The existing ideas in `live` a review changed since `was` (each one's EDITED fields, JSON-encoded), with what
# changed: [(idea, "merged into I375" | "dropped" | "edited: priority, after"), ...].
def review_changes(live: list[dict], was: dict[str, dict[str, str]]) -> list[tuple[dict, str]]:
    found = []
    for idea in live:
        fields = [k for k in EDITED if idea["id"] in was and json.dumps(idea.get(k)) != was[idea["id"]][k]]
        if "status" in fields and idea["status"] == "merged":
            found.append((idea, f"merged into {idea.get('merged_into')}"))
        elif "status" in fields and idea["status"] == "dropped":
            found.append((idea, "dropped"))
        elif fields:
            found.append((idea, f"edited: {', '.join(fields)}"))
    return found


def run_meta(repo: Path | None = None, state: Path = STATE) -> str:
    """Claude is only the review launcher; preparation/application are shared with native sessions. The worker
    runs in the background so coding and games carry on; a later call (this, or the upkeep tick's apply_finished)
    applies it once the worker has exited. A launch that fails (it can't start, the worker errs or exits early, or its
    output can't be read or applied) is retried after an hour, and a review failing MAX_LAUNCHES times is abandoned,
    so the next call prepares a fresh one: nothing a worker leaves in its folder can stop reviews for good. A launch
    a provider's quota error stopped is retried once the limit resets (when_due) but not counted
    (provider_limited)."""
    import online
    repo = repo or Path(__file__).resolve().parent.parent
    with only_one(state / "review") as mine:
        if not mine:
            return "meta busy: another caller is launching or applying the review"
        if why := quota.held(state, "review"):  # a finished one is still applied, by the upkeep (apply_finished)
            return f"meta waits for the new 5 h window ({why}; loop/quota.py)"
        work = prepare_review(repo, state)
        if work is None:
            return "meta not due"
        with locked(state / "review"):
            record = online.read(state / "review.json", {})
            if record.get("pending") != str(work):
                return "meta already applied"
            if alive(record.get("pid")):
                return "meta running in the background"
            if not record.get("pid"):
                if time.time() < when_due(record, "attempted_at", 3600):
                    return f"meta deferred: retry an unfinished/failed review {retry_when(record)}"
                launches = record.get("launches", 0)
                record.update(attempted_at=time.time(), launches=launches + 1, retry_at=None)
                online.save(state / "review.json", record)
                try:  # the rule counts are measured once, before the worker's first run
                    record["pid"] = launch_review(state, work, measure=launches == 0)
                except Exception as error:
                    return launch_failed(state, record, work, f"could not launch: {type(error).__name__}: {error}")
                online.save(state / "review.json", record)
                return f"meta launched in the background: {work}"
            record["pid"] = None
            online.save(state / "review.json", record)
        return collect(state, work)


# The upkeep tick's step (maintenance.tick): applies the pending review once its background worker has exited (a
# recorded pid no longer alive), as run_meta would, and nothing else: it never prepares or launches a review, and
# leaves a native session's review (no pid) to that session. Returns collect's outcome line, or None.
def apply_finished(state: Path = STATE) -> str | None:
    import online
    with only_one(state / "review") as mine:
        if not mine:
            return None
        with locked(state / "review"):
            record = online.read(state / "review.json", {})
            if not record.get("pending") or not record.get("pid") or alive(record["pid"]):
                return None
            work = Path(record["pending"])
            record["pid"] = None
            online.save(state / "review.json", record)
        return collect(state, work)


# The main checkout, which the drivers and the upkeep run from: its loop/ is main's current code while it is on main.
REPO = Path(__file__).resolve().parent.parent
FRESH_TIMEOUT = 600  # seconds a fresh apply process may take (an apply takes about a second)

# Every prompt that asks a model for ideas, follow-ups or map variations, besides this module's review PROMPT. Each
# carries IDEA_RULE where loop/GENERALISE.md's shared text goes (MISSES 41: each step had its own copy of the rule to
# generalise, so a fix to one prompt never reached the others); test_meta.IdeaRule fails when one lacks it or a
# loop/*.md that asks for ideas.jsonl or followups.jsonl isn't listed here.
IDEA_PROMPTS = ("loop/AUDIT.md", "loop/POSTMORTEM.md", "loop/FOLLOWON.md", "loop/MAPIDEAS.md", "loop/PROMPT.md",
                "loop/RISERS.md")
IDEA_RULE = "{idea_rule}"


# `template` with loop/GENERALISE.md in place of IDEA_RULE. Every builder of an IDEA_PROMPTS prompt (audit.prepare,
# mapideas.prepare, driver.lane_prompt and driver.postmortem_prompt) and prepare_review call it before filling in
# their own fields; the shared text holds no braces, so a str.format after it is safe.
def with_idea_rule(template: str) -> str:
    return template.replace(IDEA_RULE, (REPO / "loop/GENERALISE.md").read_text().strip())


# Runs `loop/<script> <args>` in a fresh process of the main checkout and returns the outcome line it prints (one
# starting with one of `outcomes`), for a judgement that spends a worker's limited launches (MAX_LAUNCHES): a driver
# or upkeep running for hours still holds the code it imported at start, and on 2026-09-27 a --once process's upkeep,
# on meta.py from before 660bb42, rejected a review the current code accepts, spending one of its two launches
# (MISSES 39). The caller holds no lock the child takes (only only_one's step lock, which the child never takes).
# A child that can't start, exits nonzero or prints no outcome (a checkout mid-merge, or on a branch whose meta.py
# prints "meta busy") says nothing about the worker, so `fallback`, the same apply in this process, decides instead.
def fresh_line(script: str, args: list[str], outcomes: tuple[str, ...], fallback: Callable[[], str],
               repo: Path | None = None) -> str:
    repo = repo or REPO
    try:
        done = subprocess.run([sys.executable, str(repo / "loop" / script), *args], cwd=repo, capture_output=True,
                              text=True, timeout=FRESH_TIMEOUT, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as error:
        return fallback() + f" (applied in this process: {script} {args[0]} failed to run: {type(error).__name__})"
    lines = done.stdout.splitlines()
    first = next((k for k, text in enumerate(lines) if text.startswith(outcomes)), len(lines))
    line = "\n".join(lines[first:]).strip()  # from the outcome on: anything a module printed on import is dropped
    if done.returncode != 0 or not line:
        why = (done.stderr or done.stdout).strip().splitlines()
        return fallback() + (f" (applied in this process: {script} {args[0]} exited {done.returncode}"
                             f"{', no outcome line' if done.returncode == 0 else ''}: {(why or [''])[-1][:200]})")
    return line + checkout_note(repo)


# "" while `repo` is on main, else a note for the outcome line naming what it is on, since a fresh process there
# runs that branch's code, not main's. Never raises: the outcome it annotates has already happened.
def checkout_note(repo: Path) -> str:
    try:
        branch = subprocess.run(["git", "symbolic-ref", "--short", "-q", "HEAD"], cwd=repo, capture_output=True,
                                text=True, timeout=60).stdout.strip()
    except (OSError, subprocess.SubprocessError) as error:
        return f" (applied by {repo}, whose branch git couldn't read: {type(error).__name__})"
    return "" if branch == "main" else f" (applied by {repo} on {branch or 'a detached HEAD'}, not main)"


# `meta.py apply <state> <work>`'s outcome: finish_review's line, or its failure as a rejection. collect runs it in a
# fresh process (fresh_line).
def applied_line(state: Path, work: Path) -> str:
    try:
        return finish_review(state, work)
    except Exception as error:
        return f"meta rejected: {type(error).__name__}: {error}"


# Applies the pending review `work` whose worker has exited (the caller holds only_one and has cleared its pid) in a
# fresh process of main's code (fresh_line; in this one when that process can't give an outcome), or records the
# failed launch (never relaunching, and launch_failed alerts it). Writes an applied review to host-alerts.log for the
# orchestrator and returns the line.
def collect(state: Path, work: Path) -> str:
    import online
    try:
        result = fresh_line("meta.py", ["apply", str(state), str(work)], ("meta applied", "meta rejected"),
                            lambda: applied_line(state, work))
    except Exception as error:
        result = f"meta rejected: {type(error).__name__}: {error}"
    if result.startswith("meta rejected"):
        with locked(state / "review"):
            record = online.read(state / "review.json", {})
            # Applied by someone else meanwhile (a native `review`): nothing to record or alert.
            return launch_failed(state, record, work, result) if record.get("pending") == str(work) else result
    alert(state, result)
    return result


MAX_LAUNCHES = 2  # failed launches of one worker folder before it is abandoned (a quota error isn't one)
PROVIDER_LIMITS = ("session limit", "usage limit", "rate limit")  # in a model provider's quota error


# Whether a failure's text is a model provider's quota error, which says nothing about the task it stopped: a coder's
# (driver), or a headless worker's launch (after_failure, mapideas), none of which counts it as a failed try.
def provider_limited(text: str) -> bool:
    return any(limit in text.lower() for limit in PROVIDER_LIMITS)


RESET_MARGIN_S = 300  # after a quota limit's reset, before a worker it stopped is retried
RESET = re.compile(r"resets\s+(\d{1,2})(?::(\d{2}))?\s*([ap]m)?(?:\s*\(([^)]+)\))?", re.IGNORECASE)


# When the quota limit a provider's error names resets ("You've hit your session limit · resets 2:20pm
# (Australia/Sydney)", or "resets 11am" in this machine's zone), as an epoch, read from the text's last "resets" (a
# worker's output ends with its latest error): that wall-clock time's occurrence nearest `now`, within 12 hours either
# way, so a reset that passed before a late pickup stays passed (not tomorrow's) and "resets 1am" read at 23:50 is
# tomorrow's; or None when it names no time of day (a weekly limit's "resets Oct 3, 9am" doesn't parse) or a zone
# this machine doesn't know, so the caller keeps its hour.
def limit_reset(text: str, now: float | None = None) -> float | None:
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    matches = list(RESET.finditer(text))
    match = matches[-1] if matches else None
    if not match or not (match[2] or match[3]):
        return None
    hour, minute = int(match[1]), int(match[2] or 0)
    if match[3]:
        hour = hour % 12 + (12 if match[3].lower() == "pm" else 0)
    if hour > 23 or minute > 59:
        return None
    try:
        zone = ZoneInfo(match[4].strip()) if match[4] else None
    except (ValueError, KeyError, OSError):
        return None  # a zone name this machine can't resolve: its own zone could be hours off
    now = time.time() if now is None else now
    today = datetime.fromtimestamp(now, zone).date()
    days = [today + timedelta(days=offset) for offset in (-1, 0, 1)]
    return min((datetime(d.year, d.month, d.day, hour, minute, tzinfo=zone).timestamp() for d in days),
               key=lambda at: abs(at - now))


# When a worker a provider's quota error (`reason`) stopped at `now` is retried: its reset plus RESET_MARGIN_S, at
# least RESET_MARGIN_S from `now` (a reset already passed while the limit still holds relaunches at most that often)
# and at most the caller's `wait` (a reset misread hours ahead stalls no worker past its old hour); None when `reason`
# is no quota error or names no reset, and the caller keeps its hour.
def limit_retry(reason: str, now: float | None = None, wait: float = 3600) -> float | None:
    now = time.time() if now is None else now
    reset = limit_reset(reason, now) if provider_limited(reason) else None
    return None if reset is None else min(max(reset, now) + RESET_MARGIN_S, now + wait)


# When a failed headless worker's `record` is due again: its "retry_at" (after_failure's limit_retry) when the last
# failure had one, else `wait` after its `since` stamp (failed_at, or review.json's attempted_at). Every write of
# `since` sets or drops "retry_at", so one left over never belongs to an older failure.
def when_due(record: dict, since: str, wait: float) -> float:
    return record.get("retry_at") or (record.get(since) or 0) + wait


# How an alert says when `record`'s worker is retried: "at 14:25 after the limit resets", or "after an hour".
def retry_when(record: dict) -> str:
    at = record.get("retry_at")
    return f"at {time.strftime('%H:%M', time.localtime(at))} after the limit resets" if at else "after an hour"


# Why the exited headless worker's session failed, from its output (session.json, else session.stderr's tail) in the
# protected `folder`, or None when it finished cleanly. Collects put it in their failure reason, so a provider's quota
# error reaches after_failure (provider_limited) and isn't counted.
def session_error(folder: Path) -> str | None:
    try:
        result = json.loads(worker_file(folder / "session.json"))
    except (OSError, ValueError) as error:
        result = {"is_error": True, "result": f"no readable session.json ({type(error).__name__})"}
    result = result[-1] if isinstance(result, list) and result else result
    if isinstance(result, dict) and not result.get("is_error"):
        return None
    detail = str(result.get("result") if isinstance(result, dict) else "")
    try:
        detail = detail or worker_file(folder / "session.stderr")
    except OSError:
        pass
    return detail.strip()[-300:] or "no output"


# Records a failed launch of the pending review `work` in `record` (review.json, read under its lock); at
# MAX_LAUNCHES counted failures it abandons the review: review.json drops it, keeping the reason under "abandoned", so
# the next run_meta prepares a fresh folder. A provider's quota error is retried but not counted (after_failure).
# Writes the line to host-alerts.log and returns it.
def launch_failed(state: Path, record: dict, work: Path, reason: str) -> str:
    import online
    record, abandoned = after_failure(record, work, reason, ("completed", "finished_at", "champion", "last"))
    online.save(state / "review.json", record)
    if abandoned:
        message = f"meta abandoned {work} after {MAX_LAUNCHES} failed launches: {'; '.join(record['abandoned'][-1]['failures'])}"
    elif provider_limited(reason):
        message = f"meta waiting out a provider quota limit, retried {retry_when(record)} (not counted): {reason}"
    else:
        message = f"meta launch {len(record.get('failures', []))} of {MAX_LAUNCHES} failed, retrying after an hour: {reason}"
    alert(state, message)
    return message


# A headless worker's record (review.json, audit.json) after a failed launch of `work`: the reason added to its
# "failures", or, at MAX_LAUNCHES, the folder abandoned: only the `keep` keys stay, with the folder and its failures
# appended to "abandoned" (the last 10), so the caller prepares a fresh folder next time. Returns (record, abandoned).
# A provider's quota error isn't counted (provider_limited): during the 2026-09-27 limits every launch failed so, and
# two of them would have abandoned a folder whose task was fine; it is kept as "limited" and retried once the limit
# resets ("retry_at", limit_retry; when_due), or after the caller's hour when the error names no reset.
def after_failure(record: dict, work: Path, reason: str, keep: tuple[str, ...]) -> tuple[dict, bool]:
    if provider_limited(reason):
        return {**record, "pid": None, "limited": reason[:500], "retry_at": limit_retry(reason)}, False
    record = {k: v for k, v in record.items() if k != "retry_at"}
    failures = [*record.get("failures", []), reason[:500]]
    if len(failures) < MAX_LAUNCHES:
        return {**record, "pid": None, "failures": failures}, False
    kept = {k: record[k] for k in (*keep, "abandoned") if k in record}
    kept["abandoned"] = [*kept.get("abandoned", []), {"work": str(work), "at": time.time(), "failures": failures}][-10:]
    return kept, True


# Background runs this process launched for the fillers (the sweep, the late screen, the prewarm, the adversarial-map
# search, official-map reports) and the evidence refresh, in their own session with output to `log`, at
# home.YIELD_NICE (through /usr/bin/nice, which execs the run under the same pid), so their builds, map making and
# exports on this Mac give way to dev work and to the evaluation's CPU gate and bot tests (MISSES 50: CPU points are a
# virtual clock, so only the lock's time and a turn's wall limit were at stake). A run clears its own recorded pid
# when it ends, so nothing waits for it: reap(), at each tick, polls them so a finished one is reaped rather than left
# a zombie.
CHILDREN: list[subprocess.Popen] = []


def spawn(args: list[str], cwd: Path, log: Path, env: dict | None = None) -> int:
    with log.open("a") as handle:
        child = subprocess.Popen(["/usr/bin/nice", "-n", str(YIELD_NICE), *args], cwd=cwd, stdin=subprocess.DEVNULL,
                                 stdout=handle, stderr=handle, start_new_session=True, env=env)
    CHILDREN.append(child)
    return child.pid


def reap() -> None:
    CHILDREN[:] = [child for child in CHILDREN if child.poll() is None]


CODING_STATUSES = ("starting", "running", "paused")  # worker.json statuses of a try still being coded (driver.coding_count;
# "paused": driver.unpause, a coder the usage pause stopped until the window resets)


# How long a run folder may go without experiment.json and still be a claim's new try being set up: next_experiment_id
# makes the folder, and run_experiment and native.claim write experiment.json after make_worktree's clone and syncs
# (a minute or two). An older one is a try new_experiment refused (its settings), which never gets one.
NEW_TRY_S = 3600


def run_number(folder: Path) -> int:
    return int(folder.name[1:]) if folder.name[1:].isdigit() else -1


# The newest try of each idea in `ideas` (ids), as {idea: (experiment id, its experiment.json, its folder)}. Run
# folders are read newest first until every idea has one, so a check parses the few newest records (about 1 MB each,
# 341 MB for 320 runs on 29 Sep: 1.4 s to parse them all), not every one. An idea is left out while a newer folder
# under NEW_TRY_S old (at `now`) has no experiment.json yet: that may be the idea's own next try, claimed and still
# being set up, so its newest recorded try isn't its current one (driver.finish_recorded would free a live claim).
def current_tries(state: Path, ideas, now: float | None = None) -> dict[str, tuple[str, dict, Path]]:
    wanted, found, setting_up = set(ideas), {}, False
    now = time.time() if now is None else now
    for folder in sorted((state / "runs").glob("E*"), key=run_number, reverse=True) if wanted else []:
        if found.keys() >= wanted:
            break
        try:
            row = json.loads((folder / "experiment.json").read_text())
        except FileNotFoundError:
            setting_up = setting_up or now - record_time(folder, 0) < NEW_TRY_S
            continue
        except (OSError, ValueError):
            continue
        idea = (row.get("idea") or {}).get("id") if isinstance(row, dict) else None
        if idea in wanted and idea not in found:
            found[idea] = None if setting_up else (folder.name, row, folder)
    return {idea: run for idea, run in found.items() if run}


# Each of the `trying` ideas' ({id: idea}) newest experiment in `state`/runs (current_tries), as (experiment id,
# experiment.json, worker.json or None when missing or unreadable), leaving out an idea whose newest experiment its
# history already records (an earlier try: the new one's record isn't written yet). driver.coding_count and stranded
# read tries so.
def newest_tries(state: Path, trying: dict[str, dict]) -> dict[str, tuple[str, dict, dict | None]]:
    tries = {}
    for idea, (exp, row, folder) in current_tries(state, trying).items():
        if any(str(line).startswith(f"{exp}:") for line in trying[idea].get("history") or []):
            continue
        try:
            worker = json.loads((folder / "worker.json").read_text())
        except (OSError, ValueError):
            worker = None
        tries[idea] = (exp, row, worker)
    return tries


RESULT_ID = re.compile(rb'\{"id": "([^"]+)"')  # a results.jsonl row's id, which asdict writes first


# The newest row of each experiment in `ids` in results.jsonl (`path`), as {id: row}: only lines whose id (RESULT_ID,
# read from the line's start) is wanted are parsed, so a check looks up a few rows of the 237 MB file in 0.09 s and
# 46 MB, where parsing it all took 1.3 s and 1.3 GB (29 Sep). The newest is a rejudge's, as driver.result_row reads.
def result_rows(path: Path, ids) -> dict[str, dict]:
    wanted, lines = {i.encode() for i in ids}, {}
    try:
        with path.open("rb") as handle:
            for line in handle if wanted else ():
                if (match := RESULT_ID.match(line)) and match.group(1) in wanted:
                    lines[match.group(1).decode()] = line
    except OSError:
        return {}
    rows = {}
    for exp, line in lines.items():
        try:
            rows[exp] = json.loads(line)
        except ValueError:
            continue
    return rows


# The note finish_idea writes on an idea when its try `row` (a results.jsonl row) is recorded, as run_experiment and
# native.finish write it: what tells a recorded try whose idea was updated from one whose update was lost.
def result_note(row: dict) -> str:
    return f"{row['id']}: {row['verdict']} ({row['reason']})"


# The experiment whose promising result `idea` last recorded: the newest result_note on it (or a rejudge's "E####:
# rejudged promising (...)") naming one, or None. A followup idea stands for this result, found without reading
# results.jsonl (driver.apply_judged, health.judged_followups).
PROMISING_NOTE = re.compile(r"^(E\d{4,}): (?:rejudged )?promising \(")


def promising_result(idea: dict) -> str | None:
    return newest_note(idea, PROMISING_NOTE)


# The experiment whose result `idea` last recorded, of any verdict (result_note or a rejudge's note), or None: the one
# that set its status last (driver.finish_idea writes a later note's status only while it is that one's).
RESULT_NOTE = re.compile(r"^(E\d{4,}): (?:rejudged )?(?:kept|promising|discarded|no code|blocked|error|stale|conflict) \(")


def last_result(idea: dict) -> str | None:
    return newest_note(idea, RESULT_NOTE)


def newest_note(idea: dict, pattern: re.Pattern) -> str | None:
    return next((m.group(1) for entry in reversed(idea.get("history") or [])
                 if isinstance(entry, str) and (m := pattern.match(entry))), None)


# The experiment whose candidate todo idea `idea` retests unchanged because its post-mortem queued it ("Retest E####
# alone", source postmortem:E####: driver.add_followups), else None: none is due once that experiment's judge
# discarded it (driver.retire_discarded, health.judged_followups; MISSES 98). A retest after a prerequisite tests a
# stack, not the same candidate again, and one the orchestrator or a judge queued is theirs.
def postmortem_retest(idea: dict) -> str | None:
    m = re.fullmatch(r"postmortem:(E\d{4,})", str(idea.get("source")))
    return (m.group(1) if m and idea.get("status") == "todo"
            and str(idea.get("title") or "").startswith(f"Retest {m.group(1)} alone ") else None)


# In-flight ideas (tunes too) past coding whose evaluator died, as (idea, experiment id, worker.json): the newest try's
# worker.json is past coding and names a `driver_pid` (the Claude driver, or the native `evaluate` that wrote
# "finished") no longer alive, or is a native tune's handoff that no evaluate took up in TUNED_WAIT_S
# (evaluator_alive), so nothing will evaluate or record it until someone recovers it. Any other record without
# driver_pid (from before worker_status wrote it) isn't stranded: it still counts as waiting.
def stranded(state: Path, ideas: list[dict]) -> list[tuple[dict, str, dict]]:
    trying = {i["id"]: i for i in ideas if str(i.get("status", "")).startswith("trying:")}
    now = time.time()
    return [(trying[idea], exp, worker) for idea, (exp, _, worker) in sorted(newest_tries(state, trying).items())
            if worker is not None and worker.get("status") not in CODING_STATUSES
            and not evaluator_alive(worker, now - record_time(state / "runs" / exp / "worker.json", now))]


# How long a native tune's candidate waits for its session's `evaluate` after `claim` returned "tuned" (worker.json
# "finished" from the tuner with no driver_pid, native.claim) before it counts as stranded: evaluate rewrites the
# record with its own pid as soon as it starts, so a longer wait means the session died or forgot it.
TUNED_WAIT_S = 3600


def record_time(path: Path, default: float) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return default


# Whether the process that evaluates a worker.json record's candidate (its `driver_pid`: the Claude driver, or native
# `evaluate`; driver.worker_status) may still be alive (loop_process). A record without one (from before worker_status
# wrote it) or with one that isn't a pid (a hand edit) can't say, so counts as alive; so does a native tune waiting for
# its session's evaluate (provider "tuner", no driver_pid, native.claim), but only while the record's `age` (seconds
# since it was written) is at most TUNED_WAIT_S. Used by throughput.waiting_now, stranded and native.resume. `live`:
# loop_processes' answer for the records' pids, when the caller asked it once for them all (evaluators_alive).
def evaluator_alive(record: dict, age: float = 0.0, live: set[int] | None = None) -> bool:
    if (pid := record.get("driver_pid")) is None:
        return not (record.get("provider") == "tuner" and age > TUNED_WAIT_S)
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return True
    return pid in live if live is not None else loop_process(pid)


# evaluator_alive for each (record, age), with one `ps` for all their pids (loop_processes).
def evaluators_alive(records: list[tuple[dict, float]]) -> list[bool]:
    pids = set()
    for record, _ in records:
        try:
            pids.add(int(record["driver_pid"]))
        except (KeyError, TypeError, ValueError):
            pass
    live = loop_processes(pids)
    return [evaluator_alive(record, age, live) for record, age in records]


# Whether `pid` is alive and running one of the loop's entry points (a `driver.py` or `native.py` argument in its
# command line, or this process): a pid the OS handed to another program after the loop process died reads as dead,
# where alive() alone would read it as alive for good. When `ps` can't be run, as alive().
def loop_process(pid: int) -> bool:
    return pid in loop_processes({pid})


# The pids of `pids` that loop_process finds, with one `ps` (state and command line) for them all: loop_process ran
# two a pid (alive's zombie check, then the command line), about 25 ms each, so throughput.waiting_now took about 120
# ms with two candidates waiting, each minute and at every evalline and health run (29 Sep).
def loop_processes(pids: set[int]) -> set[int]:
    found = {pid for pid in pids if pid == os.getpid()}
    asked = sorted(pid for pid in pids - found if exists(pid))
    if not asked:
        return found
    try:
        ps = subprocess.run(["ps", "-o", "pid=,stat=,command=", "-p", ",".join(map(str, asked))], capture_output=True,
                            text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return found | set(asked)
    rows = {int(parts[0]): parts[1:] for line in ps.stdout.splitlines()
            if len(parts := line.split(None, 2)) >= 2 and parts[0].isdigit()}
    if not rows and ps.returncode != 0:  # they exited since, or ps is refused here
        return found | {pid for pid in asked if alive(pid)}
    return found | {pid for pid, (stat, *command) in rows.items() if "Z" not in stat and any(
        Path(word).name in ("driver.py", "native.py") for word in (command[0] if command else "").split())}


# Whether process `pid` (a record's) still runs. A process that has exited but that its parent hasn't collected (a
# zombie: kill 0 still finds it) is dead: this process reaps it when it is its own child (os.waitpid; its status is
# lost, so a caller that wants an exit code waits on its Popen instead), and `ps` stat Z says so for another's. An
# audit worker exited ~01:17 on 2026-09-29 read as running until it was collected by hand at 01:23, as its launching
# --once never reaped it (MISSES 87). When ps can't be run, a process kill 0 finds counts as alive.
def alive(pid: object) -> bool:
    return exists(pid) and (pid == os.getpid() or not zombie(pid))


# alive() without the zombie check: `pid` is a pid kill 0 finds (this process's own exited child reaped first).
def exists(pid: object) -> bool:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False  # not a pid a record could hold: os.kill(-1, 0) would signal every process and succeed
    if pid == os.getpid():
        return True
    try:
        if os.waitpid(pid, os.WNOHANG)[0] == pid:
            return False
    except (ChildProcessError, OverflowError):
        pass  # not this process's child
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, OverflowError):
        return False
    except PermissionError:  # it exists, owned by someone else
        pass
    return True


# Whether `pid` is a zombie (ps stat Z): exited, waiting for its parent to collect it. False when ps can't say.
def zombie(pid: int) -> bool:
    try:
        ps = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return ps.returncode == 0 and ps.stdout.strip().startswith("Z")


# When process `pid` started, as `ps -o lstart=` prints it (to the second), or None when there is no such process or
# ps can't be run (a sandbox may refuse it). With the pid, it names one process: a later one given the same pid
# started later.
def process_started(pid: int) -> str | None:
    try:
        # One locale and timezone for every writer and reader: lstart's text follows both (en_AU "Mon 28 Sep", C
        # "Mon Sep 28", TZ=UTC another hour), and a native session under LC_ALL=C beside a Claude main (MISSES 60)
        # would read the other's live tickets as dead.
        ps = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, timeout=10,
                            env={**os.environ, "LC_ALL": "C", "TZ": "UTC"})
    except (OSError, subprocess.SubprocessError):
        return None
    if ps.returncode != 0:
        return None
    return ps.stdout.strip() or None


# The evaluation queue's folder under the loop home (driver.enter_queue writes it, health.evaluation_queue reads it).
QUEUE = "evaluation-queue"


# Whether the process that wrote evaluation-queue ticket `ticket` (driver.enter_queue's JSON: pid, started,
# experiment) still waits in the queue under `state`. Its pid alone can't say: loop_process is true for this
# process's own pid and whenever ps can't run, and a later driver given a dead one's pid would wait for good behind
# "its own" ticket. So: alive and started when the ticket says; where ps can't say (or an older ticket has no start
# time), its experiment still waiting for that pid (runs/<id>/worker.json "finished" with it as driver_pid) and not
# yet recorded (its experiment.json's "finished" time, saved after record: results.jsonl is ~185 MB to read each poll).
def ticket_live(state: Path, ticket: dict) -> bool:
    pid = ticket.get("pid")
    if not alive(pid):
        return False
    started = process_started(pid)
    if started and ticket.get("started"):
        return started == ticket["started"]
    exp_id = ticket.get("experiment")
    if not isinstance(exp_id, str) or not re.fullmatch(r"E\d{4,}", exp_id):
        return False
    try:
        worker = json.loads((state / "runs" / exp_id / "worker.json").read_text())
    except (OSError, ValueError):
        return False
    if not isinstance(worker, dict) or worker.get("status") != "finished" or worker.get("driver_pid") != pid:
        return False
    try:
        experiment = json.loads((state / "runs" / exp_id / "experiment.json").read_text())
    except (OSError, ValueError):
        return True
    return not (isinstance(experiment, dict) and experiment.get("finished"))


# Where the Python toolchains uv installs live: every venv's python links there, so sandboxed commands must read it.
TOOLCHAINS = "~/.local/share/uv"


# The live state folders a review or audit reads (its context and inputs point into them), passed as --add-dir with
# the repo. Never the state folder itself: every working folder can take new files, and its top level holds the
# records the loop trusts.
READS = ("champion", "runs", "study-replays", "online-replays", "evidence-replays", "scout-replays", "audits",
         "discord", "rule-games", "riser-replays")
# Claude Code tools that reach other sessions, schedules or subagents; workers get none of them (--tools, and denied).
NOT_FOR_WORKERS = ("Agent", "SendMessage", "ListAgents", "RemoteTrigger", "CronList", "CronCreate", "CronDelete",
                   "WebFetch", "WebSearch")
WORKER_TOOLS = "Bash,Read,Edit,Write,Glob,Grep"
# Flags every headless Claude session the loop starts takes (workers, coders and their resumed turns): no MCP servers
# and no skills, which none of them uses. A coder runs with --dangerously-skip-permissions, and sig's MCP servers
# (supabase-prod, the claude.ai connectors) were in its reach, beside replays and messages it reads; and the skill list
# alone was 30 KB of every coder's context (E0161: 230 skills, 2026-09-27).
LEAN_SESSION = ("--strict-mcp-config", "--disable-slash-commands")
# The rule every headless Claude session the loop starts is given as its appended system prompt (worker_command,
# driver.run_lane; a resumed coder turn keeps its first launch's, and driver.resume_coder adds it to the turn's text):
# in `claude -p` nothing re-invokes a session once its turn ends, so a turn ended "waiting for" its own background work
# was taken as the result (MISSES 63: E0197's coder, E0068's post-mortem). Claude Code blocks a lone `sleep 240` and
# suggests Monitor or run_in_background, whose notices come only in a later turn: 52 of 186 coder transcripts hit that
# block by 2026-09-28 and 68 used Monitor, E0197's the call before it ended. An until loop with a sleep inside passed
# the block in 306 coders' commands. A pid loop across Bash calls doesn't wait in a coder's sandbox, which refuses ps,
# pgrep and kill -0 on a job an earlier call started (40 of 59 such coder commands; `while kill -0` ended at once in a headless coder session on 2026-09-28), so the
# wait polls a file the work writes when it ends; that session waited out a 50 s job this way (MISSES 63).
# The generalisation principle (sig, 2026-09-28: "Make sure every possible agent that gets spawned with -p or
# otherwise knows about the generalisation principle"; earlier "don't hard code magic, hard coded hand values" and
# leave-one-out on the official maps), carried to every headless session inside FOREGROUND, which every launch appends
# (test_launches checks each launch for it).
GENERALISE = ("Generalise (sig, 2026-09-28): the bot must win on maps and against bots it has never seen (the "
              "tournaments use unreleased maps). Never add a hand-picked value, threshold or round: make the behaviour "
              "depend on what the bot observes (map size, round, both teams' counts and lengths, room, portals), or fit "
              "it from logged games with every relevant observed input. Judge anything fitted or tuned on held-out "
              "maps: each official map left out with its near_* copies out of training, maps newly added to the pool, "
              "and worldgen maps for anything fitted on our generator's alone (every SPRT and tune plays a share of "
              "fresh worldgen maps: read its per-style worldgen line apart). A result that holds only on one "
              "generator is a finding, not a pass. A constant "
              "you can't avoid states what it would depend on. Ideas and notes you write follow the same rule.")
FOREGROUND = ("You run headless: once your turn ends, nothing re-invokes this session and your final message is taken "
              "as your result. Monitor events, run_in_background completion notices and subagent results never arrive "
              "after your turn ends, so never end your turn while a command, game, check, Monitor or subagent you "
              "started still runs, and don't use Monitor or run_in_background to wait. Wait in the foreground inside "
              "one Bash call with a timeout up to 600000 ms: run the work as one blocking command, or start it with a "
              "marker it writes when it ends, `rm -f <name>.done; (<work>; echo $? > <name>.done) > <name>.log 2>&1 &` "
              "(the rm keeps a rerun under the same name from reading the last run's marker), and poll that with "
              "`until [ -f <name>.done ]; do sleep 30; done`, repeating the call until it is done. Claude Code blocks "
              "a lone `sleep N`, not a sleep inside such a loop. Don't wait on a pid across Bash calls: your sandbox "
              "refuses ps, pgrep and kill -0 on a job an earlier call started, so it reads as finished while it runs. "
              + GENERALISE)


# The repo and the READS folders that exist, less `keep` (the worker's own folder's parent, where it could otherwise
# add entries beside its work folder).
def worker_reads(state: Path, keep: str) -> tuple[Path, ...]:
    return (Path(__file__).resolve().parent.parent,
            *(state / name for name in READS if name != keep and (state / name).is_dir()))


# Settings for a headless state worker (review, replay audit, map ideas, rule counts, Discord digest or REFLECT
# list) that writes only in its `work` folder, under `state / keep`. It runs in acceptEdits mode (worker_command), not bypass, so the file tools and Bash (in the
# OS sandbox) read and write only inside its working directories (`work`, plus the `reads` passed to worker_command)
# and a request outside them is refused, never prompted. Deny rules keep Edit (and so sandboxed writes) off the keys,
# the repo, every live state entry except `keep` and every other folder in `keep` (listed exactly: a `state/*` glob
# would also block the work folder) and EXTERNAL_CODE, and reads off the secrets a working directory holds: the game
# hosts' SSH names and the browser states. No network, no web tools, no MCP servers and no tools reaching other sessions: the review
# and the map-ideas worker read Discord text written by strangers.
def worker_settings(state: Path, keep: str, work: Path) -> str:
    home = str(Path.home())
    repo = Path(__file__).resolve().parent.parent
    live = [f"Edit(/{entry}/**)" if entry.is_dir() else f"Edit(/{entry})"
            for entry in sorted(state.iterdir()) if entry.name != keep]
    live += [f"Edit(/{entry}/**)" if entry.is_dir() else f"Edit(/{entry})"
             for entry in sorted((state / keep).iterdir()) if entry != work] if (state / keep).is_dir() else []
    live += [f"Edit(/{state / name})" for name in ("STOP", "ASTRA_STOP") if not (state / name).exists()]
    secret_folders = [Path(home) / ".unswbc", state / "maintenance-browser", repo / ".gstack"]
    secret_files = [state / "remote.json"]
    secrets = [f"Read(/{p}/**)" for p in secret_folders] + [f"Read(/{p})" for p in secret_files]
    return json.dumps({
        # --setting-sources user still loads sig's user hooks, which run unsandboxed with the worker's tool inputs on
        # stdin (git-ai's checkpoint, gstack's Stop hook, plugin hooks), and his plugins: none for a worker reading
        # strangers' text. Checked on Claude Code 2.1.281: a --settings file's disableAllHooks stops user-settings
        # hooks, and per-plugin false entries drop user-enabled plugins from the session.
        "disableAllHooks": True,
        "enabledPlugins": {name: False for name in user_plugins()},
        "sandbox": {"enabled": True, "allowUnsandboxedCommands": False, "failIfUnavailable": True,
                    "filesystem": {"denyRead": [*(str(p) for p in secret_folders + secret_files), SHARED_TMP,
                                                SHARED_TMP.removeprefix("/private")],
                                   "allowRead": [TOOLCHAINS], "denyWrite": [str(EXTERNAL_CODE)]},
                    "network": {"allowedDomains": [], "deniedDomains": ["*"]}},
        "permissions": {"blockReadsOutsideWorkingDirectories": True,
                        "deny": [*secrets, f"Edit(/{home}/.unswbc/**)", f"Edit(/{repo}/**)", *live,
                                 f"Edit(/{EXTERNAL_CODE}/**)", *NOT_FOR_WORKERS]},
    })


# The plugins enabled in the user's Claude Code settings (~/.claude/settings.json), which worker_settings and
# driver.lane_settings turn off.
def user_plugins() -> list[str]:
    try:
        enabled = json.loads((Path.home() / ".claude/settings.json").read_text()).get("enabledPlugins", {})
    except (OSError, ValueError):
        return []
    return sorted(name for name, on in enabled.items() if on) if isinstance(enabled, dict) else []


# The worker reads work/PROMPT.md on stdin (open it as the process's stdin): the `claude` on PATH can be a
# wrapper (cmux) that refuses arguments over 120 KB. `reads`: folders besides its work folder (the cwd) it may read.
# `tools` and `effort` narrow a read-only report worker (loop/reports.py). `usage`: its kind for the usage gate
# (quota.gate), which raises quota.Held while the 5-hour usage level holds it; callers check quota.held first.
def worker_command(work: Path, settings: str, reads: tuple[Path, ...] = (), tools: str = WORKER_TOOLS,
                   effort: str = "medium", *, usage: str) -> list[str]:
    if why := quota.gate(STATE, usage):
        raise quota.held_error(STATE, usage, why)
    # --setting-sources user: never the worker folder's own .claude settings or hooks, which it could have written.
    return ["claude", "-p", "--model", "opus", "--effort", effort, "--permission-mode", "acceptEdits",
            "--setting-sources", "user",
            "--settings", settings, "--output-format", "json", "--tools", tools, *LEAN_SESSION,
            "--append-system-prompt", FOREGROUND, *(a for r in reads for a in ("--add-dir", str(r)))]


# The environment for a headless worker: none of the launching session's own variables (CLAUDECODE, CLAUDE_CODE_*
# such as its messaging socket and token, CLAUDE_PID) or cmux's (CMUX_*, its claude shim on PATH, its NODE_OPTIONS),
# and no API key (subscription only), nor the site's (UNSWBC_KEY, or any UNSWBC_* credential: a worker never calls the
# site). CLAUDE_CODE_OAUTH_TOKEN stays: it is how a machine without a keychain login signs in.
def worker_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_API_KEY", "CLAUDECODE", "CLAUDE_PID")
           and not (k.startswith(("CLAUDE_CODE_", "CMUX_", "CODEX_COMPANION_")) and k != "CLAUDE_CODE_OAUTH_TOKEN")
           and not (k.startswith("UNSWBC_") and any(word in k for word in ("KEY", "TOKEN", "SECRET", "PASS")))
           and k != "CLAUDE_PLUGIN_DATA"}
    env["PATH"] = ":".join(p for p in env.get("PATH", "").split(":") if "cmux-cli-shims" not in p)
    if "cmux" in env.get("NODE_OPTIONS", ""):
        env.pop("NODE_OPTIONS")
    return env


# Claude Code's temporary folder for every session of this user unless CLAUDE_CODE_TMPDIR says otherwise: it holds
# the other sessions' task output and scratchpads, so a worker's sandbox may not read it.
SHARED_TMP = f"/private/tmp/claude-{os.getuid()}"


# Where the unsandboxed side keeps a headless worker's stdin (PROMPT.md) and output (session.json, session.stderr):
# <loop home>/worker-io/<kind>/<work name>/, a live state entry, so Edit-denied to every worker (worker_settings) and
# never inside `work`, which the worker controls: a link, FIFO, folder or mode it leaves there is never opened. Made
# before the worker's settings are computed, so the deny list includes it. Reviews keep theirs in rule-games/.
def protected_io(state: Path, kind: str, work: Path) -> Path:
    folder = state / "worker-io" / kind / work.name
    folder.mkdir(parents=True, exist_ok=True)
    return folder


# Undoes a worker's own flags (chflags uchg) and mode (chmod 000) on its folder before a launch: its owner always can.
def unlock(work: Path) -> None:
    if hasattr(os, "chflags"):
        os.chflags(work, 0)
    os.chmod(work, 0o755)


# Puts the briefing (<state>/briefing.md, what every piece of research should assume) in the worker's `work` as
# briefing.md, as it is at launch, and has `folder`'s PROMPT.md name it when it doesn't yet: every launch calls this
# just before worker_io (REFLECT 2026-09-30 09:12 F4: a rule sig gave reached only the workers told by hand;
# loop/test_launches.py checks each launch). A link or file the worker left under that name is replaced, never
# followed; `work` is unlocked first, since a worker may have left it at mode 000.
def brief(state: Path, work: Path, folder: Path) -> None:
    unlock(work)
    source = state / "briefing.md"
    target = work / "briefing.md"
    if target.is_symlink() or target.exists():
        target.unlink()
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "w") as out:
        out.write(source.read_text() if source.exists() else "(no briefing yet)\n")
    prompt = folder / "PROMPT.md"
    if prompt.exists() and "briefing.md" not in prompt.read_text():
        with prompt.open("a") as out:
            out.write(f"\n\nRead {target} first: the orchestrator's list of what every piece of research should "
                      "assume (the contest's rules, sig's decisions, measured findings); it overrides older notes.\n")


# The worker's stdin, stdout and stderr in `folder` (protected_io, or a review's run folder): PROMPT.md opened as a
# plain file (one link, not a link, not blocking), session.json and session.stderr truncated. The caller closes them.
def worker_io(folder: Path) -> tuple[int, int, int]:
    prompt = os.open(folder / "PROMPT.md", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    info = os.fstat(prompt)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        os.close(prompt)
        raise OSError(f"{folder / 'PROMPT.md'} is not a plain file")
    os.set_blocking(prompt, True)
    out = os.open(folder / "session.json", os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
    err = os.open(folder / "session.stderr", os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
    return prompt, out, err


# `command` run with `work` as its working folder while the process itself starts from elsewhere (the protected
# folder), so a launch never needs the worker's folder to be enterable: a failed `cd` is the worker's failed run.
def in_folder(work: Path, command: list[str]) -> list[str]:
    return ["/bin/sh", "-c", 'cd "$1" || exit 1; shift; exec "$@"', "in_folder", str(work), *command]


# `command` (a claude session) run with a fresh private CLAUDE_CODE_TMPDIR (0700), so its sandboxed commands write
# there and not in SHARED_TMP, where the orchestrator's task output and scratchpad live. The folder is made when the
# command starts and deleted when it exits or is stopped with SIGTERM (a SIGKILL leaves it). Its path stays short:
# Claude Code falls back to SHARED_TMP for a long one. The session runs at home.WORKER_NICE (/usr/bin/nice execs it
# under the same pid; home.nice_increment from the launching process, so a launcher already niced doesn't push it to
# 20), which every command it starts inherits, even where its sandbox refuses setpriority. Every headless worker
# launch goes through here (test_launches).
def private_tmp(command: list[str]) -> list[str]:
    script = ('folder=$(mktemp -d /tmp/bcwXXXXXXXX) || exit 1; trap \'rm -rf "$folder"\' EXIT; '
              f"trap 'exit 143' TERM INT HUP; CLAUDE_CODE_TMPDIR=\"$folder\" /usr/bin/nice -n {nice_increment()} "
              '"$@"')
    return ["/bin/sh", "-c", script, "private_tmp", *command]


def git_config_digest(clone: Path) -> str:
    fd = os.open(clone / ".git/config", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as handle:
        return hashlib.sha256(handle.read(1 << 20)).hexdigest()


# Raises unless `clone`'s .git is still the folder its clone made: a real folder, with no commondir or
# config.worktree (either would make git read another config) and, when driver.make_worktree recorded it in
# <state>/runs/<clone name>/git-config.sha256, the same config. The lane's Bash could otherwise move .git aside, edit
# the config there and move it back, around driver.lane_settings' path rules. Called before any git in a coder's clone
# (driver.git_run, health.uncommitted_no_code).
def clone_intact(clone: Path, state: Path) -> None:
    dot = clone / ".git"
    if dot.is_symlink() or not dot.is_dir() or (dot / "commondir").exists() or (dot / "config.worktree").exists():
        raise RuntimeError(f"{dot} is not the clone's own git folder any more; not running git in it")
    # git follows links inside .git when it writes: a linked refs/heads would let the driver's own rebase move the
    # main repo's branches. A git folder holds only folders and plain files, so anything else stops it. One removed
    # while this walks (a quick check's or a coder's games' .tmp host status, native.coder_status) is skipped.
    for folder, subfolders, names in os.walk(dot):
        for name in subfolders + names:
            try:
                mode = os.lstat(os.path.join(folder, name)).st_mode
            except FileNotFoundError:
                continue
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise RuntimeError(f"{os.path.join(folder, name)} is not a folder or plain file; not running git in it")
    record = state / "runs" / clone.name / "git-config.sha256"
    if record.exists() and record.read_text().strip() != git_config_digest(clone):
        raise RuntimeError(f"{dot / 'config'} changed since the clone was made; not running git in it")


# An idea's headline, the title before its first colon, lowercased; empty when shorter than 30 characters (too
# generic to call two ideas the same change).
def headline(title: str) -> str:
    head = title.split(":", 1)[0].strip().lower()
    return head if len(head) >= 30 else ""


# Each idea's experiments in results.jsonl as `E#### verdict +x.x/pairs`, for waiting_ideas. Read line by line (the
# file is hundreds of MB) without the evaluation lock: a line being appended now doesn't parse and is skipped
# (retest_origins), as is any row without an idea id.
def tested_runs(results: Path) -> dict[str, list[str]]:
    tested: dict[str, dict[str, str]] = {}
    if not results.exists():
        return {}
    with results.open() as rows:
        for line in rows:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            idea = row.get("idea") if isinstance(row, dict) else None
            name = idea.get("id") if isinstance(idea, dict) else idea
            if not isinstance(name, str):
                continue
            sprt = row.get("sprt") if isinstance(row.get("sprt"), dict) else {}
            score = f" {sprt['elo']:+.1f}/{sprt.get('pairs', '?')}" if isinstance(sprt.get("elo"), (int, float)) else ""
            tested.setdefault(name, {})[row.get("id", "?")] = f"{row.get('id', '?')} {row.get('verdict', '?')}{score}"
    return {name: list(runs.values()) for name, runs in tested.items()}  # a re-judged id's latest row (latest_rows)


# Whether an idea still waits to be tested: to do, follow-up, or being tried.
def is_waiting(idea: dict) -> bool:
    return idea.get("status") in ("todo", "followup") or str(idea.get("status", "")).startswith("trying")


# backlog.md for a worker that proposes ideas (audit, post-mortem, follow-on): the waiting ideas (to do, follow-up,
# being tried), then every other idea a result tested (tried, kept, merged or dropped after one), one line each with
# its experiments as `E#### verdict +x.x/pairs` from results.jsonl. One already queued is named "same change as I###"
# with the new evidence, which append_ideas_locked merges into it (the 17:03 audit of 2026-09-26 re-proposed I240 as
# I279 without seeing the backlog), as is a part that does nothing alone without one, behind its own switch (sig's
# combine policy of 2026-09-29, LOOP.md: Prerequisites and stacks); one needing a waiting idea that changes play on its
# own takes "after" on it, and any other on the same decision says how it differs (intake_siblings flags one that names
# none); one on a tested lever names those experiments and what differs (audit 1790566937954902000's I689 re-proposed
# E0206's, E0114's and E0159's opening centre race without naming them).
def waiting_ideas(state: Path) -> str:
    path = state / "ideas.jsonl"
    ideas = [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []
    tested = tested_runs(state / "results.jsonl")

    def line(i: dict, head: str) -> str:
        runs = tested.get(i["id"])
        return f"- {i['id']} [{head}] {flat(i['title'])}" + (f" ({', '.join(runs)})" if runs else "")

    rows = [line(i, f"{i.get('category', '?')}, priority {i.get('priority', '?')}, {i['status']}")
            for i in ideas if is_waiting(i)]
    done = [line(i, f"{i.get('category', '?')}, {i['status']}"
                 + (f" into {i['merged_into']}" if i.get("status") == "merged" and i.get("merged_into") else ""))
            for i in ideas
            if not is_waiting(i) and (i.get("status") in ("tried", "kept") or i["id"] in tested)]
    return ("# Waiting ideas\n\nBefore proposing, check this section and the tested one below, and name by id in your "
            "hypothesis the waiting ideas that act on the same decision (the same category, params or code). A change "
            "already here, or a part that does nothing alone without one of them, is `same change as I###` with your "
            "new evidence, a part naming its own switch: the loop merges it into that idea, whose coder builds it too. "
            "One needing a waiting idea that changes play on its own takes `\"after\": [ids]`; otherwise say how yours "
            "differs. The loop flags a new idea that overlaps a waiting or in-flight one it doesn't name "
            "(meta.intake_siblings)."
            "\n\n" + ("\n".join(rows) or "(none)") + "\n\n"
            "# Tested ideas\n\nIdeas a result already tested, with each experiment's verdict and normalized Elo over its "
            "pairs. An idea on a lever tested here names the experiment(s), their result and conditions, and says what "
            "differs (a correction, a new condition, a changed champion). Never propose the exact change already tested "
            "unless as an explicit retest saying why its verdict may no longer hold. Cite these results as evidence "
            "with their conditions, never as a ban.\n\n" + ("\n".join(done) or "(none)") + "\n")


# The waiting ideas in full for a post-mortem or follow-on (.postmortem/ideas.jsonl): one JSON line each with its
# hypothesis, kind, prerequisites, source, evaluation settings and every history note, which backlog.md's titles leave
# out (REFLECT 2026-09-29 00:39 F23: a coder asked to tell its follow-up apart from others saw only their titles); kind
# and evaluation separate the same change from a different test of it. About 1 MB at 327 waiting ideas; it is bounded
# by the backlog, and each turn rewrites it.
def waiting_ideas_full(state: Path) -> str:
    path = state / "ideas.jsonl"
    ideas = [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []
    fields = ("id", "category", "priority", "status", "kind", "title", "hypothesis", "after", "source", "evaluation",
              "history")
    return "".join(json.dumps({k: i[k] for k in fields if k in i}) + "\n" for i in ideas if is_waiting(i))


# "same change as I###" (or "same idea as") in a proposal's text: group 1 is the existing idea's id. "not the same
# change as I###" is no such claim.
SAME_CHANGE = re.compile(r"(?<!\bnot )(?<!\bnot the )\bsame (?:change|idea) as (I\d+)\b", re.I)


# The idea new idea `idea` calls itself the same change as (SAME_CHANGE), or None: read from its title and its
# hypothesis before the Options line, whose remedies may name a waiting idea this one isn't.
def same_change(idea: dict) -> str | None:
    before = flat(idea.get("hypothesis", "")).partition("Options:")[0]
    return match.group(1) if (match := SAME_CHANGE.search(f"{idea.get('title', '')} {before}")) else None


# The live backlog's rows (<state>/ideas.jsonl), none when it doesn't exist.
def live_ideas(state: Path) -> list[dict]:
    path = state / "ideas.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


# Adds validated new ideas to the live backlog with fresh ids and `source`; returns their ids. One that declares
# itself the same change as an existing idea, or has the same headline as a waiting one, is added merged into it (its
# text reaches that idea's coder, and it never runs again unchanged once that idea was tested: I179 repeated E0082's
# switch).
def append_ideas(state: Path, proposals: list[dict], source: str) -> list[str]:
    with locked(state / "ideas.jsonl"):
        return append_ideas_locked(state, proposals, source)


# The idea that carries `name`'s change on: `name` followed through merges ("status": "merged", "merged_into") in
# `by_id`, as driver.prerequisites reads each `after` entry.
def merge_target(name: str, by_id: dict[str, dict]) -> str:
    seen = {name}
    while (into := by_id.get(name, {}).get("merged_into")) and by_id[name].get("status") == "merged" \
            and into not in seen:
        seen.add(into)
        name = into
    return name


# Whether `start` waits on `target`: its `after` entries (through merges, merge_target) name `target`, or name an
# idea still to do or being built that waits on it in turn. An idea waiting on itself this way is never claimed
# (driver.ready_ideas); health.stuck_ideas alerts on one.
def waits_on(start: str, target: str, by_id: dict[str, dict]) -> bool:
    stack, seen = [start], set()
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        for entry in by_id.get(name, {}).get("after") or []:
            prerequisite = merge_target(entry, by_id)
            status = str(by_id.get(prerequisite, {}).get("status", ""))
            if prerequisite == target:
                return True
            if status == "todo" or status.startswith("trying:"):
                stack.append(prerequisite)
    return False


# Called when `row` is merged into `kept` (append_ideas_locked, apply_review), with `by_id` the backlog as it will be
# written (`row` merged in it): `kept` takes what the merged row carried that it lacks, a better priority (a lower
# number) and prerequisites (`after`), with a history note naming the row. An `after` that resolves to `kept` through
# merges, or that waits on `kept` itself, is left out: `kept` would wait on itself for good. So is an `after` of `kept`'s
# own that now resolves to `kept`, the row merged into an idea stacked on it (I191 into I375, which waits on I191).
# Before, the merged row's priority was dropped: I356 (1) into I191 (2), I310 (1) into I280 (2) (MISSES 38).
def absorb(kept: dict, row: dict, by_id: dict[str, dict]) -> None:
    if own := [a for a in kept.get("after") or [] if merge_target(a, by_id) == kept["id"]]:
        kept["after"] = [a for a in kept["after"] if a not in own]
        kept.setdefault("history", []).append(f"no longer waits on {', '.join(own)}: merged into this")
    notes = []
    if isinstance(row.get("priority"), int) and isinstance(kept.get("priority"), int) \
            and row["priority"] < kept["priority"]:
        notes.append(f"priority {kept['priority']} -> {row['priority']}")
        kept["priority"] = row["priority"]
    extra = [a for a in row.get("after") or [] if a not in (kept.get("after") or [])
             and merge_target(a, by_id) != kept["id"] and not waits_on(merge_target(a, by_id), kept["id"], by_id)]
    if extra:
        notes.append(f"after {', '.join(extra)}")
        kept["after"] = [*(kept.get("after") or []), *extra]
    if notes:
        kept.setdefault("history", []).append(f"{row['id']} merged into this: {'; '.join(notes)} from it")


# Merges a new idea `row` (a proposal given its id, or a review's new idea) into the waiting idea it is a duplicate
# of (twin_target), with the note twin_target gives when it can't. `ideas` is the backlog it joins, `added` the new
# rows before it, candidates too.
def merge_twin(row: dict, ideas: list[dict], added: list[dict]) -> None:
    target, note = twin_target(row, ideas + [i for i in added if i is not row])
    if note:
        row.setdefault("history", []).append(note)
    if target:
        row.update(status="merged", merged_into=target)
    if row["status"] == "merged":
        by_id = {i["id"]: i for i in ideas + added + [row]}
        absorb(by_id[row["merged_into"]], row, by_id)


# The waiting idea in `others` (the backlog new idea `row` joins) that `row` merges into, and the note `row`'s
# history takes when it names one that can't take it: one its text calls the "same change as" (same_change), or one
# with its headline. A tried idea is never run again, so a row naming one stays to be tested, with a note. A review's
# new idea comes with its text already renumbered (apply_review), so "same change as I401" about the review's own I401
# finds it, not a post-mortem's I401 queued meanwhile.
# A followup is never claimed, only its retest (the queue audit of 2026-09-27: 14 merged ideas pointed at followups,
# I495 into I129), so a twin that is a followup takes the row through its open retest, else the row stays to be tested
# on its own, with a note. merge_twin merges by it, and refusal asks it whether a same-change row needs Options.
def twin_target(row: dict, others: list[dict]) -> tuple[str | None, str | None]:
    waiting = {i["id"] for i in others if i["status"] in ("todo", "followup") or i["status"].startswith("trying")}
    heads = {headline(i["title"]): i["id"] for i in others if i["status"] in ("todo", "followup") and headline(i["title"])}
    named = same_change(row)
    if named and named != row["id"] and named in waiting:
        target = named
    elif named and named in {i["id"] for i in others}:
        return None, f"names {named} as the same change, which is no longer waiting"
    else:  # the same headline as a waiting idea: I163 and I180 both ran as separate ideas
        target = heads.get(headline(row.get("title", ""))) if headline(row.get("title", "")) else None
    by_id = {i["id"]: i for i in others}
    if target and by_id[target]["status"] == "followup":
        if retests := open_retests(by_id[target], others):
            return retests[0], None
        return None, (f"the same change as {target}, a followup with no retest waiting: queued to be tested on its "
                      "own")
    return target, None


# The waiting retests (todo or being built) of followup `idea`, oldest first: ideas titled "Retest E####" (driver.RETEST)
# for an experiment that tested it, read from its history's "E####: <verdict> ..." notes (driver.finish_idea), each
# followed through merges (merge_target: I150's retest I585 was merged into I808, its waiting retest). All 25
# followups' histories named exactly their results.jsonl experiments on 2026-09-27.
def open_retests(idea: dict, ideas: list[dict]) -> list[str]:
    tested = {m.group(1) for note in idea.get("history") or [] if (m := re.match(r"(E\d{4,}): ", str(note)))}
    by_id = {i["id"]: i for i in ideas}
    found = []
    for i in ideas:
        if (m := re.match(r"Retest (E\d{4,})\b", i.get("title") or "")) and m.group(1) in tested:
            retest = by_id.get(merge_target(i["id"], by_id), i)
            if (retest["status"] == "todo" or retest["status"].startswith("trying")) and retest["id"] not in found:
                found.append(retest["id"])
    return found


# Every new idea is checked against the waiting backlog as it joins, whoever wrote it (sig, 2026-09-29: "make sure any
# additional ideas added by whatever anything also think about combining too"): before, only a declared "same change
# as" merged, and a split finding or a complement waited for the next review's "Split findings" (sibling_clusters).
# Each pair sibling_pairs links with a new row of `added` (the backlog `ideas`, those being coded
# too, plus the new rows, by the same measure and bars; one the row names, or a merged row, is not a pair) gives the
# pair's newer row, once, a history note "flagged at intake as a possible duplicate or complement of I### (score x),
# ..." (INTAKE_NOTE, which idea_names ignores, so the review still lists the pair until someone decides), an idea being
# coded marked "in flight" (a duplicate of work already underway costs most). Returns one "I### ~ I### (x), ..." per
# noted row, for the alert; none, without scoring, when no new row is one sibling_scored takes (a generator's rows).
def intake_siblings(ideas: list[dict], added: list[dict]) -> list[str]:
    new = {row["id"]: row for row in added if sibling_scored(row)}
    if not new:
        return []
    coding = {i["id"] for i in ideas if str(i.get("status") or "").startswith("trying:")}
    pairs, _ = sibling_pairs(ideas + added, set(new))
    matches: dict[str, list[str]] = defaultdict(list)
    for a, b, score in pairs:
        row, other = (b, a) if b in new else (a, b)
        matches[row].append(f"{other}{' in flight' if other in coding else ''} (score {score:.2f})")
    for row, found in matches.items():
        new[row].setdefault("history", []).append(
            f"{INTAKE_NOTE} {', '.join(found)}, for the orchestrator or next review to merge, stack or tell apart; "
            "build only this idea's change")
    return [f"{row} ~ {', '.join(found)}" for row, found in matches.items()]


# intake_siblings for a caller about to save `added` into `ideas`, as the alert line to raise once they are saved
# (intake_alert), or None when nothing matched. The check is advisory: an error in it goes into that line and stderr,
# never up to block the save.
def intake_check(ideas: list[dict], added: list[dict], where: str = "") -> str | None:
    try:
        flagged = intake_siblings(ideas, added)
    except Exception as error:
        print(f"intake sibling check failed{where}: {error!r}", file=sys.stderr)
        return f"intake{where}: the sibling check failed ({type(error).__name__}); the ideas were saved without it"
    return (f"intake{where}: {'; '.join(flagged)}: merge a duplicate, or a part that does nothing alone behind its own "
            "switch; stack a prerequisite that changes play with \"after\"; else note how they differ "
            "(driver.orchestrator_note)") if flagged else None


# Raises intake_check's line after the save: an alert that can't be written never makes the caller retry the save
# (and queue the ideas twice).
def intake_alert(state: Path, line: str | None) -> None:
    if line:
        try:
            alert(state, line)
        except OSError as error:
            print(f"could not alert {line!r}: {error}", file=sys.stderr)


# append_ideas for a caller already holding locked(state / "ideas.jsonl"), which read the backlog to decide what to add.
def append_ideas_locked(state: Path, proposals: list[dict], source: str) -> list[str]:
    path = state / "ideas.jsonl"
    ideas = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    number = max((int(i["id"][1:]) for i in ideas if re.fullmatch(r"I\d+", i["id"])), default=0)
    added = []
    for idea in proposals:
        number += 1
        row = dict(idea, id=f"I{number:03d}", status="todo", source=source)
        merge_twin(row, ideas, added)
        added.append(row)
    flagged = intake_check(ideas, added)
    temporary = state / "ideas.jsonl.tmp"
    temporary.write_text(ideas_text(ideas + added))
    temporary.replace(path)
    intake_alert(state, flagged)
    import decisions
    if decisions.AUTOMATIC.match(source):  # queued with no orchestrator in between: each needs its decision
        try:  # the ideas are written: a listing that fails must not make a caller retry and queue them again
            decisions.applied(state, source, added)
        except OSError as error:
            alert(state, f"could not list applied ideas for {source}: {error}; decide on {[i['id'] for i in added]} "
                  "by hand")
    return [idea["id"] for idea in added]


# What happens to a result's code next, in plain words beside its verdict word (REFLECT 2026-09-28 17:52 F13: sig read
# E0245's "H1" then "promising" as "discarded when it was good"). evalline's result line and driver's RESULT alert.
FATES = {"kept": "the new champion",
         "promising": "not champion; code kept on cand/{id}, its idea waits for a retest",
         "discarded": "not champion; its coder writes a post-mortem, code kept on cand/{id}",
         "no code": "nothing was built; its idea is marked tried (queued again when its coder did no work)",
         "stale": "its idea goes back to be coded again on the new champion, work kept as wip/{id}",
         "blocked": "its idea goes back to be coded again, work kept as wip/{id}",
         "error": "its idea goes back to be coded again (retired after a second error), work kept as wip/{id}"}


def fate(verdict: str, exp_id: str) -> str:
    return FATES.get(verdict, "").format(id=exp_id)


# `text` on one line: every whitespace run (newlines, \r, U+2028/2029 included) one space. driver.flat is this.
def flat(text) -> str:
    return " ".join(str(text).split())


# `idea` with its title and category flattened (flat), as every new idea enters the backlog (valid_proposals and a
# review's new ideas in apply_review): the stats and waiting-ideas lists and the coders' prompts put both inside a
# line, where a line break could start a line that reads as a notes heading or a labelled note (MISSES 46).
def one_line(idea: dict) -> dict:
    return {**idea, **{k: flat(idea[k]) for k in ("title", "category") if isinstance(idea.get(k), str)}}


# The valid proposals among `lines` (JSON objects: category, priority, title, hypothesis, optional evaluation, kind,
# tune, target and after, so a post-mortem or audit can propose tune, speed, identical, noloss, formula and targeted
# ideas and order them after others), checked like new review ideas, never banning a behaviour by citing a result and
# with no bare threshold in its rule unless it says why (bare_thresholds) and, with `options` (every idea someone
# wrote; off for the loop's own templates: retests, re-checks, quick fixes, sweeps, screen ports, late-screen fixes),
# an Options line of MIN_OPTIONS or more (options_count), which a "same change as" row merging into a waiting idea of
# `ideas` (the live backlog, live_ideas) needs none of (refusal). At most `limit`. Titles and categories come out on
# one line (one_line). Each line refused (not an idea, or refusal) is named on stderr and added to `refused` as
# (line, reason) for the caller to report with report_refused and its run folder; with no `refused` list given,
# report_refused alerts them itself, with no file.
def valid_proposals(lines: list[str], limit: int, options: bool = True,
                    refused: list[tuple[str, str]] | None = None, ideas: list[dict] | None = None) -> list[dict]:
    proposals, mine = [], [] if refused is None else refused
    for line in lines:
        if reason := proposal_fault(line, options, ideas):
            if line.strip():
                print(f"refused {line[:80]!r}: {reason}", file=sys.stderr)
                mine.append((line, reason))
            continue
        proposals.append(one_line({k: v for k, v in json.loads(line).items() if k in PROPOSAL_FIELDS}))
    if refused is None and mine:
        report_refused(STATE, None, "valid_proposals", mine)
    return proposals[:limit]


PROPOSAL_FIELDS = ("category", "priority", "rank", "title", "hypothesis", "evaluation", "kind", "tune", "target",
                   "after")


# Why proposal `line` is refused by valid_proposals, or None when it is a valid idea.
def proposal_fault(line: str, options: bool = True, ideas: list[dict] | None = None) -> str | None:
    try:
        idea = json.loads(line)
    except json.JSONDecodeError:
        return "not JSON"
    if not isinstance(idea, dict) or "constraints" in idea:  # constraints go inside "tune", as valid_backlog says
        return "not an idea object (constraints go inside tune)"
    idea = one_line({k: v for k, v in idea.items() if k in PROPOSAL_FIELDS})
    if "after" in idea and not (isinstance(idea["after"], list) and all(isinstance(a, str) for a in idea["after"])):
        return "its after is not a list of idea ids"
    idea = dict(idea, id="I0", status="todo")
    if not idea_fields_ok(idea):
        return "not a valid idea: it needs a category, a priority 1-5, a title and a hypothesis (idea_fields_ok)"
    if fault := new_idea_fault(idea):
        return fault
    return refusal(idea, options, ideas)


REFUSED_FILE = "refused-ideas.jsonl"
REFUSED_KEEP = 50  # refused proposals one call saves
REFUSED_TEXT = 20_000  # characters kept of each saved proposal
REFUSED_ALERTS = 5  # alert lines one call writes; the rest are counted on one more


# Every refused proposal reaches the orchestrator, whatever wrote it: a coder still on a prompt from before a new
# check (the Options line, 2026-09-28) writes follow-ups it refuses, and add_followups logged only "follow-ups were
# invalid; none queued" with the reason on stderr. One host-alerts.log line per refusal from `source` (its reason and
# the text's start), up to REFUSED_ALERTS, and, with a `folder` (the caller's run folder, one the worker can't write),
# each saved in <folder>/refused-ideas.jsonl as {"source", "reason", "line"}, rewritten each call: at most
# REFUSED_KEEP lines of REFUSED_TEXT characters, about 1 MB. Re-add one by hand, its text fixed, with
# append_ideas(state, valid_proposals([line], 1), source).
def report_refused(state: Path, folder: Path | None, source: str, refused: list[tuple[str, str]]) -> None:
    if not refused:
        return
    where = ""
    if folder is not None:
        try:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / REFUSED_FILE).write_text("".join(
                json.dumps({"source": source, "reason": reason, "line": line[:REFUSED_TEXT]}) + "\n"
                for line, reason in refused[:REFUSED_KEEP]))
            where = f"; text in {folder / REFUSED_FILE}"
        except OSError as error:
            where = f"; could not save the text ({error})"
    for line, reason in refused[:REFUSED_ALERTS]:
        alert(state, f"{source}: refused a proposal: {flat(reason)[:300]}{where}: {flat(line)[:300]}")
    if len(refused) > REFUSED_ALERTS:
        alert(state, f"{source}: refused {len(refused) - REFUSED_ALERTS} more proposal(s){where}")


# Why new idea `idea` is refused, or None, checked in this order: it keys on a known map's identity (MAP_IDENTITY:
# tournament maps are unseen), bans a behaviour by citing a result (bans_by_result), has a bare threshold in its rule
# without saying why (bare_thresholds) or, with `options`, lacks an Options line of MIN_OPTIONS or more
# (options_count). One written "same change as I###" (same_change) that merges into a waiting idea of the
# backlog `ideas` it joins (twin_target) needs no Options line: it is evidence for that idea, not a new remedy (E0320's
# and E0322's post-mortems lost theirs so, 2026-09-29); one naming a tried idea, or with no `ideas` given, is a new
# idea and needs one. valid_proposals and a review's new ideas (apply_review) both ask it.
def refusal(idea: dict, options: bool = True, ideas: list[dict] | None = None) -> str | None:
    if known := MAP_IDENTITY.search(f"{idea.get('title', '')}. {idea.get('hypothesis', '')}"):
        return (f"keys on map identity ({known.group(0)!r}): every tournament map is unseen and seeding doesn't "
                "matter (sig, 30 Sep), so it pays only on maps we know (MISSES 102)")
    if ban := BAN.search(f"{idea.get('title', '')}. {idea.get('hypothesis', '')}"):
        return f"bans a behaviour by citing a result: {ban.group(0).strip()!r}"
    if found := bare_thresholds(idea):
        hidden = "a hand cutoff behind a param name, " if PARAM_THRESHOLD.fullmatch(found[0][0]) else ""
        return (f"bare threshold {found[0][0]!r} ({hidden}in {found[0][1][:160]!r}); state it over what the bot "
                "observes (a param as its formula, 'P.x = <rounds left, lengths, distances, map size ...>'), or say "
                "'bare threshold because ...' (loop/GENERALISE.md)")
    if not options or (count := options_count(idea)) >= MIN_OPTIONS:
        return None
    if (named := same_change(idea)) and ideas is not None \
            and twin_target(idea, [i for i in ideas if i["id"] != idea["id"]])[0]:
        return None
    why = f"; it can't merge into {named}, so it is a new idea" if named and ideas is not None else ""
    return f"its Options line lists {count} numbered remedies, not {MIN_OPTIONS}+{why} (loop/GENERALISE.md)"


# Starts the review detached and returns its pid: loop/rulecounts.py first (with `measure`, the review's first launch)
# plays the champion's rule-count games into rule-counts.md (M17), then becomes the worker. Its log, stdin (the saved
# prompt) and output live in the review's protected run folder, never in `work`, which the worker controls; the
# worker's own flags and modes on `work` are undone first, since its owner always can. It runs at home.WORKER_NICE
# from the start, its analysis too (it was at nice 0 until the worker's exec, 29 Sep 16:05: this Mac at load 36).
def launch_review(state: Path, work: Path, measure: bool = True) -> int:
    import rulecounts
    folder = rulecounts.run_folder(state, work)
    folder.mkdir(parents=True, exist_ok=True)
    unlock(work)
    with rulecounts.log_file(state, work) as log:
        return subprocess.Popen(["/usr/bin/nice", "-n", str(nice_increment()), sys.executable,
                                 str(Path(__file__).resolve().parent / "rulecounts.py"), "review",
                                 str(state), str(work), *(["--measure"] if measure else [])], cwd=folder,
                                stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True).pid


# Applies a review whose worker has exited, if it finished cleanly; raises when it didn't, with session_error's reason
# (so launch_failed sees a provider's quota error). Its output is in the protected run folder (in `work` for a review
# launched before that, read with worker_file's checks either way).
def finish_review(state: Path, work: Path) -> str:
    import rulecounts
    output = rulecounts.run_folder(state, work) / "session.json"
    try:
        response = json.loads(worker_file(output if output.exists() else work / "session.json"))
    except (OSError, ValueError):
        response = {}
    if isinstance(response, list):
        response = next((row for row in reversed(response) if row.get("type") == "result"), {})
    if not isinstance(response, dict) or not response or response.get("is_error"):
        why = session_error(output.parent if output.exists() else work) or "no result"
        raise RuntimeError(f"review worker failed: {why}; retained {work}")
    return apply_review(state, work)


# Copies study data into a coder's clone, under its gitignored data/ folder, for a coder whose sandbox can't read the
# loop home or the orchestrator's scratchpad (memory note coder-sandbox-reads):
#
#     .venv/bin/python loop/meta.py copy-data E0404 ~/.bc-loop/research/kings            # a folder, within the cap
#     .venv/bin/python loop/meta.py copy-data E0404 --files a.jsonl b.jsonl              # named files, any size
#
# A copy over data_cap is refused unless its files are named one by one (--files: `listed`), so a large copy is a
# choice, not an accident (REFLECT 2026-09-30 15:13 F29: the king data copied into E0404's clone came to 5 GB). The cap
# is DATA_SHARE of the disk's free space over driver.IN_FLIGHT, the clones being coded or waiting at once, each of
# which may get a copy (older clones keep theirs: cleanup deletes no data): 2% of 145 GB free over 8 is 360 MB (30 Sep).
# The clone is the coder's to write, so nothing is written through a link, even one swapped in mid-copy (MISSES 17):
# the files are copied first into a temporary folder beside the clone, which the coder can't write, then hard-linked
# into place through folders each opened by the one before it (dir_fd, O_NOFOLLOW | O_DIRECTORY), so a link anywhere
# on the way fails the open; a link never replaces an existing name. A clash, or any failure, removes what this copy
# put in the clone: no partial copy is left.
DATA_SHARE = 0.02


def data_cap(clone: Path) -> int:
    import driver  # lazily: driver imports this module
    return int(shutil.disk_usage(clone).free * DATA_SHARE / driver.IN_FLIGHT)


def copy_data(clone: Path, sources: list[Path], listed: bool = False) -> str:
    files: list[tuple[Path, Path]] = []  # (source file, its path under data/)
    for source in sources:
        if source.is_dir() and not listed:
            files += [(p, source.name / p.relative_to(source)) for p in sorted(source.rglob("*"))
                      if p.is_file() and not p.is_symlink()]
        elif source.is_file():
            files.append((source, Path(source.name)))
        else:
            raise ValueError(f"{source}: {'a folder: --files names each file' if source.is_dir() else 'no such file'}")
    total, cap = sum(p.stat().st_size for p, _ in files), data_cap(clone)
    if total > cap and not listed:
        raise ValueError(f"{len(files)} files, {total / 1e6:.0f} MB, over the {cap / 1e6:.0f} MB cap ({DATA_SHARE:.0%} "
                         f"of the disk's free space over the clones in flight): copy only what the coder needs, "
                         f"naming each file with --files")
    staging = Path(tempfile.mkdtemp(prefix=f".copy-data-{clone.name}-", dir=clone.parent))
    folders: dict[tuple[str, ...], int] = {}  # open folder fds under the clone, by their parts
    made: list[tuple[int, str, bool]] = []  # (parent fd, name, is a folder) this copy put in the clone, in order
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW

    def folder_fd(parts: tuple[str, ...]) -> int:
        if parts not in folders:
            parent = folder_fd(parts[:-1])
            try:
                os.mkdir(parts[-1], 0o755, dir_fd=parent)
                made.append((parent, parts[-1], True))
            except FileExistsError:
                pass
            try:
                folders[parts] = os.open(parts[-1], flags, dir_fd=parent)
            except OSError as error:
                raise ValueError(f"{clone.joinpath(*parts)} is not a real folder: nothing written through it") from error
        return folders[parts]

    try:
        folders[()] = os.open(clone, flags)
        for k, (source, _) in enumerate(files):
            shutil.copyfile(source, staging / str(k))
        for k, (_, relative) in enumerate(files):
            parent = folder_fd(("data", *relative.parent.parts))
            try:
                os.link(staging / str(k), relative.name, dst_dir_fd=parent, follow_symlinks=False)
            except FileExistsError as error:
                raise ValueError(f"{clone / 'data' / relative} already exists: nothing copied") from error
            made.append((parent, relative.name, False))
    except BaseException:
        for parent, name, is_folder in reversed(made):
            with suppress(OSError):
                if is_folder:
                    os.rmdir(name, dir_fd=parent)
                else:
                    os.unlink(name, dir_fd=parent)
        raise
    finally:
        for fd in folders.values():
            os.close(fd)
        shutil.rmtree(staging, ignore_errors=True)
    return f"copied {len(files)} files, {total / 1e6:.1f} MB, into {clone / 'data'} (cap {cap / 1e6:.0f} MB)"


if __name__ == "__main__":
    if sys.argv[1:2] == ["apply"]:  # meta.py apply <state> <work>: collect's fresh process
        print(applied_line(Path(sys.argv[2]), Path(sys.argv[3])))
    elif sys.argv[1:2] == ["copy-data"]:
        import argparse
        from home import clones_folder
        parser = argparse.ArgumentParser(prog="meta.py copy-data", description="copy study data into a coder's clone")
        parser.add_argument("experiment", help="E####, whose clone gets the copy")
        parser.add_argument("sources", nargs="+", type=Path)
        parser.add_argument("--files", action="store_true", help="the sources are files named one by one: no size cap")
        args = parser.parse_args(sys.argv[2:])
        clone = clones_folder(REPO) / args.experiment
        if not re.fullmatch(r"E\d{4,}", args.experiment) or not clone.is_dir():
            sys.exit(f"meta.py copy-data: no clone {clone}")
        try:
            print(copy_data(clone, args.sources, args.files))
        except (ValueError, OSError) as error:
            sys.exit(f"meta.py copy-data refused: {error}")
    else:
        print(run_meta())
