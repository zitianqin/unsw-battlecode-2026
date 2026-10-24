"""Checks the engine doesn't make: kelp, portals and dragons are mirrored, output is deterministic and unchanged
for the old styles, near maps are fair variants of their source map, trap maps keep dragons out of the pocket, and
deadend maps put their fast tiles in dead ends, fortress maps have the fortress shape, portalrooms maps put their fast
tiles behind portals and macro maps start a long dragon (dead_end, fortress_shape, portal_gated_count, sealed and
long_start, all checked against the organiser maps), repearled near maps keep their source's layout with a pearl
field drawn afresh, worldgen maps come from the pinned vendored file, mirrored and in our format, portal-bet maps
hold their death portals and vaults (death_cells, vault_tiles), sealedstart maps seal each team's start room
(sealed_start), and curves maps have diagonal walls (diagonal_wall, checked against the organiser maps).

    uv run python -m mapgen.test_gen
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter

from mapgen.gen import (
    BIG_MIN,
    DIRS,
    KELP,
    LADDER,
    LONG_START,
    MIN_REACH,
    MAZE,
    RETIRED,
    SEALED_ROOM,
    SEALED_ROOM_PEARLS,
    SMALL_MAX,
    SPRINT,
    WORLDGEN,
    Board,
    bodies_adjacent,
    boxed_heads,
    check,
    community_pick,
    community_sources,
    dead_end_count,
    diagonal_wall,
    DIAGONAL_MIN,
    chebyshev,
    death_cells,
    edge_tiles,
    fast,
    long_start,
    portal_gated_count,
    sealed,
    sealed_start,
    sealed_room,
    keeps_rooms,
    pocket_shape,
    pockets,
    ROOM_CHANGE,
    spread,
    team_tiles,
    fortress_shape,
    portal_only,
    dragon_teams,
    generate,
    ladder_maps,
    ladder_names,
    map_area,
    near_sources,
    motions,
    pearl_change,
    repearled,
    REPEARL_MIN_CHANGE,
    move_board,
    near_ok,
    parse,
    render,
    sized_sources,
    vault_tiles,
    PORTALBET_DEATHS,
    PORTALBET_MIN_AREA,
    VAULT_SHARE,
    VISION,
)

SEEDS = range(60)
# sha256 of generate(seed, style) for seeds 0-11: normal, odd and mix recorded before the near and eval styles were
# added, the rest before small and big were. Appending a style must leave every earlier one's maps unchanged.
OLD_STYLE_HASHES = {
    # Changed on purpose 2026-09-27: half of normal and odd maps got a walled border, and nine in ten fast farms.
    "normal": "54ba51d336b64a21cf3f89268e421a338031202282454ca5e7e8a5502661b2d1",
    "odd": "8a45a7dcd2e8814ddd7b194732cc46c087954b079d7e2e39560e5dd36090e421",
    "mix": "324efb1de0c3b21e35bf99875d50ede49b14394aaeedb10dbf2fa64fc0013505",
    # Changed on purpose 2026-09-27: Portals and Slithery Fight joined the near sources (maps/retired kept the rest).
    # And again 2026-09-27: about half the near maps (seeds 2-8 and 11 here, by a hash of the seed; mapgen.repearled)
    # redraw their pearl field; the rest are byte for byte as before.
    # And 2026-09-30: near keeps each source's sealed start rooms (gen.keeps_rooms), so the copies of Portals,
    # Stronghold and Trauma (seeds 4, 8 and 9 here) changed.
    # And again 2026-09-30: near keeps each source's portal pockets and apart teams (gen.kept_parts), so the copies of
    # Default, Portals, Queen Of Spades, Slithery Fight and Autarky (seeds 1, 4, 5, 7 and 11 here) changed.
    "near": "5e1d8880a613852365a14b8f162346fc5842923d6c91aa440d48c8e0b48a9571",
    # Changed on purpose 2026-09-27: seeds 7 and 16 (seed % 9 == 7) became deadend maps, seed 8 (seed % 9 == 8) a
    # fortress map, and the mix seeds changed with mix; then seed 4 (seed % 9 == 4) a macro map.
    # And the near seeds 3 and 6 were repearled (mapgen.repearled), the organisers' maps that look the same with other
    # pearl timings.
    # And again 2026-09-27: the community map an eval community seed varies is a hash of the seed (mapgen.community_pick,
    # MISSES 59), since picking by seed // 9 gave each community map one opponent seat; of seeds 1 and 10 here, seed 1
    # changed source map (Great Divide to Portal Labyrinth) and seed 10 kept its own.
    # And 2026-09-29: seed 2 (seed % 18 == 2, half the last mix ninth) became a curves map.
    # And 2026-09-30: seeds 1 and 10 (Portal Labyrinth and Great Divide copies) changed, since near keeps their sealed
    # start rooms (gen.keeps_rooms).
    # And again 2026-09-30: seeds 1, 3 and 10 (copies of sources with portal pockets) changed (gen.kept_parts).
    "eval": "97ff27e0ddff0ad1f70856ce4a0c59883bca868d6aff4045f4f1fcaed71b1e57",
    "trap": "454993cba3df38da84c596189baae6067a50684ddf0d57fd6bed24a8492f6e85",
    # Changed on purpose 2026-09-30: Autarky's and Dilemma's portal pockets are kept (gen.kept_parts), every seed.
    "sprint": "d05098974f6d75654918d74d07d037615c9910ad3cb69cee98ec99ddc2389468",
}


def portal_pairs(board: Board) -> dict[int, frozenset]:
    ends: dict[int, set] = {}
    for e, k in board.edges.items():
        if k >= 0:
            ends.setdefault(k, set()).add(e)
    return {k: frozenset(v) for k, v in ends.items()}


# With self_mirror_ok, a portal pair may be its own mirror (both ends on the symmetry line, like Trophy's).
# `generated`: one of our own maps, whose starting dragons are 3-6 long by design; the organisers' maps and their
# near variants can start longer (Autarky: 14).
def check_symmetric(text: str, self_mirror_ok: bool = False, generated: bool = True) -> None:
    board = parse(text)
    for t, gap in board.gaps.items():
        assert board.gaps[board.mirror(t)] == gap, t
    for e, k in board.edges.items():
        if k == KELP:
            assert board.edges.get(board.mirror_edge(e)) == KELP, e
    pairs = portal_pairs(board)
    all_pairs = set(pairs.values())
    for ends in pairs.values():
        assert len(ends) == 2 and len({e[0] for e in ends}) == 1
        mirrored = frozenset(board.mirror_edge(e) for e in ends)
        assert mirrored in all_pairs
        assert (self_mirror_ok and mirrored == ends) or not (mirrored & ends)
        assert len({board.mirror_edge(e) == e for e in ends}) == 1  # both ends on the line or both off it
    teams = [int(line.split()[1]) for line in text.splitlines() if line.startswith("DRAGON ")]
    assert teams == [i % 2 for i in range(len(teams))]
    for a, b in zip(board.dragons[::2], board.dragons[1::2]):
        assert [board.mirror(t) for t in a] == b
        assert 3 <= len(a) <= 6 if generated else len(a) >= 2


def test_maps_are_symmetric() -> None:
    for seed in SEEDS:
        for style in ("normal", "odd"):
            check_symmetric(generate(seed, style))


# Trap maps (a pearl pocket behind narrow doors) are mirrored, pass the reachability check and start no dragon in the
# pocket, which is where nearly all their pearls are.
def test_trap_maps_are_symmetric() -> None:
    for seed in SEEDS:
        text = generate(seed, "trap")
        assert f"MAP_NAME gen-trap-{seed}" in text
        check_symmetric(text)
        board = parse(text)
        assert check(board), seed
        spawning = [g for g in board.gaps.values() if g[1] > 0]
        pocket_gap = max(set(spawning), key=spawning.count)  # the pocket is 9+ tiles, other pearl spots 1-8
        assert not any(board.gaps[t] == pocket_gap for body in board.dragons for t in body), seed


def test_deterministic() -> None:
    assert generate(7, "mix") == generate(7, "mix")
    assert generate(7, "normal") != generate(8, "normal")


def test_old_styles_unchanged() -> None:
    for style, digest in OLD_STYLE_HASHES.items():
        h = hashlib.sha256()
        for seed in range(12):
            h.update(generate(seed, style).encode())
        assert h.hexdigest() == digest, style


def test_ladder_sources_are_valid() -> None:
    assert len(ladder_names()) == 11  # 8 live, 3 retired on 2026-09-27
    assert [p.stem for p in near_sources()[11:]] == ["autarky", "dilemma"]
    for path in near_sources():
        text = path.read_text()
        check_symmetric(text, self_mirror_ok=True, generated=False)
        assert near_ok(parse(text), parse(text)), path.stem


# Moving a map by any motion keeps it symmetric (edge and portal mapping included) and passing the checks.
def test_motions_keep_symmetry() -> None:
    for path in near_sources():
        name, source = path.stem, parse(path.read_text())
        for m in motions(source)[::7]:
            moved = move_board(source, m)
            check_symmetric(render(moved, name), self_mirror_ok=True, generated=False)
            assert near_ok(moved, source), (name, m)


# `source` moved by the motion that best reproduces `board`'s kelp, portals and dragon tiles: the moved source a near
# variant was nudged from.
def aligned_source(board: Board, source: Board) -> Board:
    def tiles(b: Board) -> set:
        return {t for body in b.dragons for t in body}

    def overlap(moved: Board) -> int:
        return len(set(moved.edges.items()) & set(board.edges.items())) + len(tiles(moved) & tiles(board))

    moved = [move_board(source, m) for m in motions(source)]
    return max((b for b in moved if (b.w, b.h) == (board.w, board.h)), key=overlap)


# True when some motion of `source` reproduces `board`'s kelp, portals and dragons, i.e. the variant is only a
# relabelled copy (perhaps with other pearl rates).
def same_up_to_motion(board: Board, source: Board) -> bool:
    for m in motions(source):
        if [[m.tile(source, t) for t in b] for b in source.dragons] != board.dragons:
            continue
        moved = move_board(source, m)
        if (moved.w, moved.h, moved.edges) == (board.w, board.h, board.edges):
            return True
    return False


def test_near_maps_are_fair_variants() -> None:
    paths = near_sources()
    names = [p.stem for p in paths]
    for seed in range(2 * len(names)):
        text = generate(seed, "near")
        name = names[seed % len(names)] + ("_repearl" if repearled(seed) else "")
        assert f"MAP_NAME near_{name}_{seed}\n" in text
        check_symmetric(text, self_mirror_ok=True, generated=False)
        board = parse(text)
        source_text = paths[seed % len(names)].read_text()
        source = parse(source_text)
        assert near_ok(board, source)
        assert dragon_teams(text) == dragon_teams(source_text)
        assert sorted(len(b) for b in board.dragons) == sorted(len(b) for b in source.dragons)
        assert not same_up_to_motion(board, source), seed
        # Recognisably similar: kelp count within 15% and the same portal count.
        assert abs(board.kelp_count() - source.kelp_count()) <= 0.15 * source.kelp_count(), seed
        assert sum(k >= 0 for k in board.edges.values()) == sum(k >= 0 for k in source.edges.values())


# Every third eval map is near, spread evenly over near_sources() (ladder and Sprint maps); a ninth are variants of the
# community maps, picked by a hash of the seed (community_pick, so no community map follows the opponent seat);
# another ninth each are the trap, deadend, fortress and macro maps, an eighteenth curves maps, and the rest the mix
# maps of the same seed.
def test_eval_is_a_third_near() -> None:
    names = [p.stem for p in near_sources()]
    community = [p.stem for p in community_sources()]
    sources, community_seen = [], []
    for seed in range(6 * len(names)):
        text = generate(seed, "eval")
        if seed % 3 and seed % 9 == 1:
            community_seen.append((seed, next(n for n in community if f"MAP_NAME near_{n}_{seed}\n" in text)))
            continue
        if seed % 3 and seed % 9 == 5:
            assert text == generate(seed, "trap"), seed
            continue
        if seed % 3 and seed % 9 == 7:
            assert text == generate(seed, "deadend"), seed
            continue
        if seed % 3 and seed % 9 == 8:
            assert text == generate(seed, "fortress"), seed
            continue
        if seed % 3 and seed % 9 == 4:
            assert text == generate(seed, "macro"), seed
            continue
        if seed % 18 == 2:
            assert text == generate(seed, "curves"), seed
            continue
        if seed % 3:
            assert text == generate(seed, "mix"), seed
            continue
        pick = seed // 3
        suffix = "_repearl" if repearled(seed) else ""
        name = next(n for n in names if f"MAP_NAME near_{n}{suffix}_{seed}\n" in text)
        assert name == names[pick % len(names)], seed
        sources.append(name)
        check_symmetric(text, self_mirror_ok=True, generated=False)
        assert near_ok(parse(text), parse(near_sources()[names.index(name)].read_text()))
    assert sorted(sources) == sorted(names * 2)
    assert community_seen == [(seed, community[community_pick(seed) % len(community)]) for seed, _ in community_seen]
    assert len({name for _, name in community_seen}) == len(community)


# Half of the near maps (eval's and --style near's) are repearled: the source's layout (kelp, portals and dragons, moved
# and nudged as any near map's) with a pearl field drawn afresh, mirrored, passing the same checks, and differing from
# the source's pearls by far more than a plain near map does; each source gets both kinds over a set. Like the
# organiser maps (126 of 420 fast tiles, DEAD_ENDS_MEASURED), a good share of their fast tiles sit in dead ends
# (fill_dead_ends: 32% on these seeds; mix-drawn farms alone gave 5% on 20 eval maps).
def test_repearled_maps_keep_the_layout_and_redraw_the_pearls() -> None:
    paths = near_sources()
    n = len(paths)
    dead, fast_tiles = 0, 0
    for seed in range(4 * n):
        text = generate(seed, "near")
        source_text = paths[seed % n].read_text()
        board, source = parse(text), parse(source_text)
        check_symmetric(text, self_mirror_ok=True, generated=False)  # pearl gaps too
        assert near_ok(board, source), seed
        assert dragon_teams(text) == dragon_teams(source_text)
        assert sorted(len(b) for b in board.dragons) == sorted(len(b) for b in source.dragons)
        assert abs(board.kelp_count() - source.kelp_count()) <= 0.15 * source.kelp_count(), seed
        assert sum(k >= 0 for k in board.edges.values()) == sum(k >= 0 for k in source.edges.values())
        change = pearl_change(aligned_source(board, source).gaps, board.gaps)
        repearl = "_repearl_" in text.split("MAP_NAME ", 1)[1].split("\n", 1)[0]
        assert repearl == repearled(seed), seed
        assert change >= REPEARL_MIN_CHANGE if repearl else change < REPEARL_MIN_CHANGE, (seed, change)
        if repearl:
            dead, fast_tiles = (a + b for a, b in zip((dead, fast_tiles), dead_end_count(board)))
    assert dead >= 0.2 * fast_tiles, (dead, fast_tiles)
    for start in (0, 12345678):  # each source's near maps of a 2,400-map set, --style near's and eval's
        for every in (1, 3):
            kinds: dict[int, set[bool]] = {}
            for seed in range(start, start + 2400, every):
                kinds.setdefault(seed // every % n, set()).add(repearled(seed))
            assert len(kinds) == n and all(k == {True, False} for k in kinds.values()), (start, every, kinds)


# The dead-end rule on the organiser maps: (fast tiles in dead ends, fast tiles) by file stem, as the measurement of
# 2026-09-27 (a standalone script, pockets.py) found them, which dead_end reproduces.
DEAD_ENDS_MEASURED = {
    "autarky": (12, 14), "big_empty": (0, 0), "default": (0, 0), "default_small": (0, 28), "devil": (0, 30),
    "dilemma": (10, 12), "portals": (24, 104), "queen_of_spades": (20, 32), "schooltime": (0, 26),
    "slithery_fight": (36, 139), "stronghold": (12, 12), "trauma": (12, 12), "trophy": (0, 11),
}


def test_dead_ends_on_the_organiser_maps() -> None:
    counts = {p.stem: dead_end_count(parse(p.read_text()))
              for p in sorted([*LADDER.glob("*.map"), *RETIRED.glob("*.map"), *SPRINT.glob("*.map")])}
    assert counts == DEAD_ENDS_MEASURED, counts


# Deadend maps are mirrored, pass the reachability check, start no dragon in a pocket, and have at least half their
# fast tiles in dead ends by dead_end, the measured shape of the ladder maps from Stronghold on.
def test_deadend_maps_put_fast_tiles_in_dead_ends() -> None:
    for seed in range(40):
        text = generate(seed, "deadend")
        assert f"MAP_NAME gen-deadend-{seed}" in text
        check_symmetric(text)
        board = parse(text)
        assert check(board), seed
        dead, fast = dead_end_count(board)
        assert fast and dead >= fast / 2, (seed, dead, fast)


# fortress_shape on the organiser maps picks out the five whose shape it was measured on (our five worst, 2026-09-27).
def test_fortress_shape_on_the_organiser_maps() -> None:
    shaped = {p.stem for p in [*LADDER.glob("*.map"), *RETIRED.glob("*.map"), *SPRINT.glob("*.map")]
              if fortress_shape(parse(p.read_text()))}
    assert shaped == {"devil", "portals", "slithery_fight", "stronghold", "trauma"}, shaped


# Fortress maps are mirrored, pass the reachability check and have the shape (the builder keeps only those; the
# inventory's own script, measure.py, found it on all of seeds 1000-1089, 33 of them the portal kind, on 2026-09-27);
# about a third are the portal kind.
def test_fortress_maps_have_the_fortress_shape() -> None:
    portal = 0
    for seed in range(30):
        text = generate(seed, "fortress")
        assert f"MAP_NAME gen-fortress-{seed}" in text
        check_symmetric(text)
        board = parse(text)
        assert check(board) and fortress_shape(board), seed
        assert 2 <= len(board.dragons) // 2 <= 4, seed
        portal += portal_only(board)
    assert 5 <= portal <= 15, portal


def organiser_maps() -> dict[str, Board]:
    return {p.stem: parse(p.read_text()) for p in [*LADDER.glob("*.map"), *RETIRED.glob("*.map"), *SPRINT.glob("*.map")]}


# portal_gated_count and sealed on the organiser maps (2026-09-27): half or more of the fast tiles are reached only
# through portals on the five maps below (Portals 64 of 104, whose other 40 are either in the open or in 2-tile cells
# nothing reaches), and only Portals seals its teams apart.
def test_portal_gates_on_the_organiser_maps() -> None:
    maps = organiser_maps()
    gated = {name for name, b in maps.items() if (n := portal_gated_count(b))[1] and n[0] >= n[1] / 2}
    assert gated == {"portals", "queen_of_spades", "schooltime", "stronghold", "trauma"}, gated
    assert portal_gated_count(maps["portals"]) == (64, 104)
    assert {name for name, b in maps.items() if sealed(b)} == {"portals"}


# Portalrooms maps are mirrored, pass the reachability check (a sealed one each team's half of it) and have at least
# half their fast tiles reached only through portals; about a third are sealed like Portals.
def test_portalrooms_maps_gate_fast_tiles_behind_portals() -> None:
    closed = 0
    for seed in SEEDS:
        text = generate(seed, "portalrooms")
        assert f"MAP_NAME gen-portalrooms-{seed}" in text
        check_symmetric(text)
        board = parse(text)
        is_sealed = sealed(board)
        assert is_sealed == ("-sealed" in text), seed
        assert check(board, MIN_REACH / 2 if is_sealed else MIN_REACH), seed
        gated, fast = portal_gated_count(board)
        assert fast and gated >= fast / 2, (seed, gated, fast)
        closed += is_sealed
    assert 10 <= closed <= 30, closed


# Portal-bet maps are mirrored and sound (bets_ok's checks). A death portal is a portal whose far end lies between two
# kelp-walled one-tile cells (death_cells: only Portals of the organiser maps has such cells, 16), and every death cell
# is such an end (none made by chance, as gen-vault-48-decoy's second one was), out of any view that holds its mouth
# (more than 2 * VISION tiles, Chebyshev), and so is a portalbet vault door's far end, the wall edge it lands on (its
# only other portal pair; vault_door holds vault maps' doors to it too, which their ordinary pairs hide here). Deathportal maps have 1-3 a team, portalbet maps PORTALBET_DEATHS a team and
# no other portal but the vault's (sig: "9 auto kill portals and 1 good portal") on PORTALBET_MIN_AREA+ tiles, and
# vault maps one only when marked decoy. Vault and portalbet maps give each team a vault reached only through portals
# (vault_tiles), fast pearls on every tile, twice VAULT_SHARE of the board in all.
def test_portal_bet_maps() -> None:
    assert {name: n for name, b in organiser_maps().items() if (n := len(death_cells(b)))} == {"portals": 16}
    for seed in range(20):
        for style in ("deathportal", "vault", "portalbet"):
            text = generate(seed, style)
            assert f"MAP_NAME gen-{style}-{seed}" in text
            check_symmetric(text)
            board = parse(text)
            assert check(board) and not boxed_heads(board) and bodies_adjacent(board), (style, seed)
            cells, area = death_cells(board), board.w * board.h
            ends: dict[int, list] = {}
            for e, k in board.edges.items():
                if k >= 0:
                    ends.setdefault(k, []).append(e)
            deaths = [(e, mouth) for pair in ends.values() for e, mouth in (pair, pair[::-1])
                      if edge_tiles(board, e) <= cells]
            assert {t for e, _ in deaths for t in edge_tiles(board, e)} == cells, (style, seed)
            assert all(chebyshev(board, a, b) > 2 * VISION for e, mouth in deaths for a in edge_tiles(board, e)
                       for b in edge_tiles(board, mouth)), (style, seed)
            doors = [pair for pair in ends.values() if not any(edge_tiles(board, e) <= cells for e in pair)]
            assert style != "portalbet" or all(chebyshev(board, a, b) > 2 * VISION for e, mouth in doors
                                               for a in edge_tiles(board, e) for b in edge_tiles(board, mouth)), seed
            lethal, rich = len(deaths) // 2, sum(fast(board.gaps[t]) for t in vault_tiles(board)) / area
            if style == "deathportal":
                assert 1 <= lethal <= 3, (seed, lethal)
            elif style == "vault":
                assert lethal == ("-decoy" in text), (seed, lethal)
            else:
                assert lethal == PORTALBET_DEATHS and len(ends) == 2 * (PORTALBET_DEATHS + 1), (seed, lethal, len(ends))
                assert area >= PORTALBET_MIN_AREA, (seed, area)
            assert style == "deathportal" or 2 * VAULT_SHARE[0] <= rich <= 2 * VAULT_SHARE[1], (style, seed, rich)


# sealed_start on the organiser maps: Trauma, Portals and Stronghold start each team in a room it leaves only by
# portal. Sealedstart maps (Trauma's opening, 29 Sep) are mirrored, load in the engine (referee/mapcheck.py) and are
# sound, and every one seals each team's start: a full-height strip of SEALED_ROOM of the width at its end holding
# every dragon of the team and at most SEALED_ROOM_PEARLS of the spawning tiles, whose only ways out are portals. The
# teams meet in the middle, as on Trauma (sealed, which Portals is, never).
def test_sealedstart_maps_seal_every_start_room() -> None:
    import sys
    sys.path.insert(0, str(LADDER.parent.parent / "referee"))
    from mapcheck import engine_fault
    assert {name for name, b in organiser_maps().items() if sealed_start(b)} == {"trauma", "portals", "stronghold"}
    for seed in range(20):
        text = generate(seed, "sealedstart")
        assert f"MAP_NAME gen-sealedstart-{seed}-" in text
        check_symmetric(text)
        assert engine_fault(text.encode()) is None, seed
        board = parse(text)
        assert check(board) and not boxed_heads(board) and bodies_adjacent(board), seed
        assert sealed_start(board) and not sealed(board), seed
        mine, _ = team_tiles(board)
        room = spread(board, mine, board.plain_step)
        width = len({x for x, _ in room})
        assert 20 <= board.h <= 28 and len(room) == width * board.h, (seed, width)
        assert SEALED_ROOM[0] * board.w - 1 <= width <= SEALED_ROOM[1] * board.w + 1, (seed, width)
        spawning = [t for t in board.all_tiles() if board.gaps[t][1] > 0]
        rooms = room | {board.mirror(t) for t in room}
        assert sum(t in rooms for t in spawning) <= SEALED_ROOM_PEARLS * len(spawning), seed


# diagonal_wall on the organiser maps (2026-09-29): six outline a picture or a lane in 45-degree kelp staircases of
# DIAGONAL_MIN edges or more; the other seven's longest are 0-4.
def test_diagonal_walls_on_the_organiser_maps() -> None:
    walls = {name: diagonal_wall(b) for name, b in organiser_maps().items()}
    assert {name: n for name, n in walls.items() if n >= DIAGONAL_MIN} == {
        "default_small": 8, "queen_of_spades": 7, "schooltime": 7, "slithery_fight": 13, "trauma": 9, "trophy": 8,
    }, walls
    assert max(n for n in walls.values() if n < DIAGONAL_MIN) == 4, walls


# Curves maps are mirrored, pass the reachability check with every body laid across open edges and every head free,
# and each has a diagonal wall of DIAGONAL_MIN edges or more; some are taichis (only on xy-mirrored boards).
def test_curves_maps_have_diagonal_walls() -> None:
    taichis = 0
    for seed in SEEDS:
        text = generate(seed, "curves")
        assert f"MAP_NAME gen-curves-{seed}" in text
        check_symmetric(text)
        board = parse(text)
        assert check(board) and bodies_adjacent(board) and not boxed_heads(board), seed
        assert diagonal_wall(board) >= DIAGONAL_MIN, seed
        if "-taichi" in text:
            assert board.sym == "xy" and board.w % 2 == board.h % 2 == 0, seed
            taichis += 1
    assert 5 <= taichis <= 25, taichis


# long_start on the organiser maps: Dilemma, Autarky and Slithery Fight start a dragon of LONG_START or more, and only
# Slithery Fight's (25, coiled) have no free first move.
def test_long_starts_on_the_organiser_maps() -> None:
    maps = organiser_maps()
    assert {name for name, b in maps.items() if long_start(b) >= LONG_START} == {"autarky", "dilemma", "slithery_fight"}
    assert {name for name, b in maps.items() if boxed_heads(b)} == {"slithery_fight"}


# Macro maps are mirrored, pass the reachability check, and each team starts one dragon of LONG_START-25 and 1-6 of 2-7;
# about a third coil the long one round its boxed-in head.
def test_macro_maps_start_one_long_dragon() -> None:
    coiled = 0
    for seed in SEEDS:
        text = generate(seed, "macro")
        assert f"MAP_NAME gen-macro-{seed}" in text
        check_symmetric(text, generated=False)
        board = parse(text)
        assert check(board), seed
        lengths = sorted(len(b) for b in board.dragons[::2])
        assert LONG_START <= lengths[-1] <= 25 and 1 <= len(lengths) - 1 <= 6, (seed, lengths)
        assert all(2 <= n <= 7 for n in lengths[:-1]), (seed, lengths)
        boxed = boxed_heads(board)
        assert ("-coil" in text) == bool(boxed) and all(len(b) == lengths[-1] for b in boxed), seed
        coiled += bool(boxed)
    assert 10 <= coiled <= 30, coiled


# The maze style (an idea's map mix) makes only fair near variants of the community Portal Labyrinth.
def test_maze_is_only_portal_labyrinth_variants() -> None:
    for seed in range(6):
        text = generate(seed, "maze")
        assert f"MAP_NAME near_portal_labyrinth_{seed}\n" in text, seed
        check_symmetric(text, self_mirror_ok=True, generated=False)
        assert near_ok(parse(text), parse(MAZE.read_text()))


# The sprint style (a Sprint-aimed test's share, an idea's evaluation "maps") makes only fair near variants of
# maps/sprint, alternating between them: none of the ladder or community maps.
def test_sprint_is_only_sprint_variants() -> None:
    for seed in range(8):
        text = generate(seed, "sprint")
        source = ("autarky", "dilemma")[seed % 2]
        assert f"MAP_NAME near_{source}_{seed}\n" in text, seed
        check_symmetric(text, self_mirror_ok=True, generated=False)
        assert near_ok(parse(text), parse((SPRINT / f"{source}.map").read_text()))


# The nearportal style (a standing share of every test, meta.PORTAL_STYLES) makes only fair near variants of the portal
# ladder maps Trauma, Portals and Trophy, in turn.
def test_nearportal_is_only_portal_ladder_variants() -> None:
    for seed in range(6):
        text = generate(seed, "nearportal")
        source = ("trauma", "portals", "trophy")[seed % 3]
        assert f"MAP_NAME near_{source}_{seed}\n" in text, seed
        check_symmetric(text, self_mirror_ok=True, generated=False)
        assert near_ok(parse(text), parse((LADDER / f"{source}.map").read_text()))


# A near variant keeps each sealed start room of its source (keeps_rooms: Trauma's strips, Portals' halves). Opening
# a kelp edge of a Trauma start room's wall is refused when it unseals the room (into the open middle) or joins the
# two teams' rooms (on the seam: still small and portal-bound, so sealed_room alone passes it) or grows it past
# ROOM_CHANGE; one into a small walled pocket of the middle is kept. Nearportal copies of Trauma and Portals keep every
# room.
def test_near_keeps_sealed_start_rooms() -> None:
    source = parse((LADDER / "trauma.map").read_text())
    head = source.dragons[0][0]
    room = sealed_room(source, head)
    assert room and all(sealed_room(source, body[0]) for body in source.dragons)
    walls = [source.side(t, d) for t in room for d, (dx, dy) in DIRS.items()
             if source.edges.get(source.side(t, d)) == KELP and source.wrap(t[0] + dx, t[1] + dy) not in room]
    foes = team_tiles(source)[1]
    unsealed = joined = kept = 0
    for wall in walls:
        opened = parse((LADDER / "trauma.map").read_text())
        opened.open_edge(wall)
        grown = sealed_room(opened, head)
        unsealed += grown is None
        joined += bool(grown and grown & foes)
        fits = bool(grown and not grown & foes and len(grown) <= ROOM_CHANGE[1] * len(room))
        kept += fits
        assert keeps_rooms(opened, source) == fits, wall
    assert unsealed and joined and kept
    for seed in (0, 1, 3, 4, 6, 7):
        name = ("trauma", "portals", "trophy")[seed % 3]
        assert keeps_rooms(parse(generate(seed, "nearportal")), parse((LADDER / f"{name}.map").read_text())), seed


# A near variant keeps its source's portal pockets with their laps (pocket_shape) and pearls, and teams that never meet
# (sealed; E0405, I1079). Portals has twelve 2x2 rooms each on an 8-tile lap and sixteen one-tile dead ends, its teams
# apart; before 2026-09-30 fresh copies kept 2-10 of the laps and every one joined the teams. Opening a room's wall,
# or the kelp between the halves, is refused; nearportal's Portals copies keep all of it, pocket pearls within
# ROOM_CHANGE.
def test_near_keeps_portal_pockets() -> None:
    text = (LADDER / "portals.map").read_text()
    source = parse(text)
    shape = Counter(pocket_shape(source))
    assert shape == Counter({(4, 8): 12, (1, 0): 16}) and sealed(source)
    room = next(r for r in pockets(source) if len(r) == 4)
    wall = next(source.side(t, d) for t in sorted(room) for d in DIRS if source.edges.get(source.side(t, d)) == KELP)
    opened = parse(text)
    opened.open_edge(wall)
    assert not keeps_rooms(opened, source)
    halves = [spread(source, tiles, source.step) for tiles in team_tiles(source)]
    cut = next(source.side(t, d) for t in sorted(halves[0]) for d, (dx, dy) in DIRS.items()
               if source.edges.get(source.side(t, d)) == KELP and source.wrap(t[0] + dx, t[1] + dy) in halves[1])
    joined = parse(text)
    joined.open_edge(cut)
    assert not sealed(joined) and not keeps_rooms(joined, source)
    # Joined through a new portal pair instead (an open edge of team A's half to one of team B's, and its mirror pair),
    # every room and pocket keeps its shape, so only the sealed clause refuses it.
    kept = {t for r in pockets(source) for t in r}
    a, b = ([("v", x, y) for x, y in sorted(half - kept) if ("v", x, y) not in source.edges
             and edge_tiles(source, ("v", x, y)) <= half - kept] for half in halves)
    ported = parse(text)
    pid = max(ported.edges.values()) + 1
    ported.edges |= {a[0]: pid, b[0]: pid, ported.mirror_edge(a[0]): pid + 1, ported.mirror_edge(b[0]): pid + 1}
    assert not sealed(ported) and not shape - Counter(pocket_shape(ported)) and not keeps_rooms(ported, source)

    def rate(board: Board) -> float:
        return sum(2 / sum(board.gaps[t]) for r in pockets(board) for t in r if board.gaps[t][1])

    for seed in (1, 4, 7, 10, 13, 16):
        copy = parse(generate(seed, "nearportal"))
        assert not shape - Counter(pocket_shape(copy)) and sealed(copy), seed
        assert ROOM_CHANGE[0] <= rate(copy) / rate(source) <= ROOM_CHANGE[1], seed


# The small and big styles (tests aimed at one size of map, review M28) make fair near variants of every ladder and
# community map of that size, in turn, and none of the other size; the Sprint-like maps (the `sprint` style) never.
def test_sized_styles_keep_to_their_size() -> None:
    all_maps = ladder_maps() + community_sources()
    for style, fits in (("small", lambda a: a <= SMALL_MAX), ("big", lambda a: a >= BIG_MIN)):
        names = [p.stem for p in sized_sources(style)]
        assert names == [p.stem for p in all_maps if fits(map_area(p.read_text()))], style
        for seed in range(len(names)):
            text = generate(seed, style)
            assert f"MAP_NAME near_{names[seed]}_{seed}\n" in text, (style, seed)
            assert fits(map_area(text)), (style, seed)
            check_symmetric(text, self_mirror_ok=True, generated=False)
            assert near_ok(parse(text), parse(sized_sources(style)[seed].read_text())), (style, seed)
    assert {"default_small", "trophy", "snake_trap"} <= {p.stem for p in sized_sources("small")}
    assert not {"dilemma", "autarky"} & {p.stem for p in sized_sources("small") + sized_sources("big")}
    assert {"big_empty", "stronghold", "unsw"} <= {p.stem for p in sized_sources("big")}


# The vendored worldgen.py as sig allowed it (mapgen/worldgen/README.md): a changed file is a new version, which needs its
# own read and go-ahead, not an edit.
WORLDGEN_SHA256 = "7134bfe6cd24e5efff48a277b618db5618dea569dce6e600bbeb8a93e2673ee2"


# The worldgen style (an independent generator's maps, an idea's evaluation "maps") runs only the pinned file and writes
# its maps as our other styles are (render's format, round-tripping through parse), named after the seed and worldgen's
# own style and landmark: mirrored, with every head reaching the whole board (worldgen's repair opens every pocket).
def test_worldgen_maps_are_pinned_mirrored_and_ours() -> None:
    assert hashlib.sha256(WORLDGEN.read_bytes()).hexdigest() == WORLDGEN_SHA256
    for seed in range(3):
        text = generate(seed, "worldgen")
        assert re.search(rf"^MAP_NAME gen-worldgen-{seed}-[a-z]+-[a-z]+$", text, re.M), seed
        board = parse(text)
        assert render(board, f"gen-worldgen-{seed}") == re.sub(r"MAP_NAME .*", f"MAP_NAME gen-worldgen-{seed}", text)
        check_symmetric(text, self_mirror_ok=True, generated=False)
        assert check(board, reach=1.0), seed


# worldgen's own pick for seed 4703010 lays dragon 0's segments 8 and 9 across a portal edge, which the engine refused
# ("dragon 0 segment 9 is not adjacent to the one before it", 29 Sep); the style picks another of that seed's worlds.
def test_worldgen_never_writes_a_body_across_a_portal() -> None:
    from mapgen.gen import worldgen_module

    assert not bodies_adjacent(parse(worldgen_module().generate(4703010).text()))
    text = generate(4703010, "worldgen")
    assert bodies_adjacent(parse(text))
    assert re.search(r"^MAP_NAME gen-worldgen-4703010-[a-z]+-[a-z]+$", text, re.M)
    check_symmetric(text, self_mirror_ok=True, generated=False)


# Where the process pool can't start (the map-ideas worker's sandbox denies its semaphores), --out makes the same
# maps one by one.
def test_generate_all_falls_back_to_one_by_one() -> None:
    from unittest.mock import patch

    import mapgen.gen as gen
    expected = [generate(seed, "mix") for seed in range(3)]
    with patch.object(gen, "ProcessPoolExecutor", side_effect=PermissionError("Operation not permitted")):
        assert list(gen.generate_all(range(3), "mix")) == expected


# From 2026-09-27 21:00 AEST replays' map text may withhold pearl timings (TILE lines removed, cut to `TILE x y`, or
# `TILE x y ? ?`). Before, parse raised IndexError or ValueError on the last two, and read the first as no pearls.
def test_parse_reads_withheld_pearl_timings_as_unknown() -> None:
    import re
    text = (LADDER / "trophy.map").read_text()
    real = parse(text)
    assert real.unknown == set() and any(gap != (0, 0) for gap in real.gaps.values())
    for cut in (re.sub(r"^TILE \d+ \d+ .*\n", "", text, flags=re.M),
                re.sub(r"^(TILE \d+ \d+) .*$", r"\1", text, flags=re.M),
                re.sub(r"^(TILE \d+ \d+) .*$", r"\1 ? ?", text, flags=re.M)):
        board = parse(cut)
        assert len(board.unknown) == board.w * board.h == 625
        assert (board.edges, board.dragons) == (real.edges, real.dragons)
        assert set(board.gaps.values()) == {(0, 0)}  # stats still run
    one = text.replace("TILE 3 4 ", "TILE 3 4 ? ? ", 1)
    assert parse(one).unknown == {(3, 4)}
    assert parse(text.replace("TILE 3 4 ", "TILE 3 4 -1 -1 ", 1)).unknown == {(3, 4)}  # a placeholder, not a gap
    # A file that lists fewer tiles than every one, and says so in its TILE_COUNT, gives the rest no pearls.
    assert parse("MAP 4 4\nSYMMETRY xy\nTILE_COUNT 1\nTILE 0 0 1 5\nEND\n").unknown == set()


# unittest (tools/merge.sh runs `-m unittest mapgen.test_gen`) finds no TestCase here: each test_ function is one.
def load_tests(loader, tests, pattern):
    import unittest
    return unittest.TestSuite(unittest.FunctionTestCase(f) for name, f in sorted(globals().items())
                              if name.startswith("test_") and callable(f))


if __name__ == "__main__":
    test_parse_reads_withheld_pearl_timings_as_unknown()
    test_maps_are_symmetric()
    test_trap_maps_are_symmetric()
    test_deterministic()
    test_old_styles_unchanged()
    test_ladder_sources_are_valid()
    test_motions_keep_symmetry()
    test_near_maps_are_fair_variants()
    test_eval_is_a_third_near()
    test_repearled_maps_keep_the_layout_and_redraw_the_pearls()
    test_dead_ends_on_the_organiser_maps()
    test_deadend_maps_put_fast_tiles_in_dead_ends()
    test_fortress_shape_on_the_organiser_maps()
    test_fortress_maps_have_the_fortress_shape()
    test_portal_gates_on_the_organiser_maps()
    test_portalrooms_maps_gate_fast_tiles_behind_portals()
    test_portal_bet_maps()
    test_sealedstart_maps_seal_every_start_room()
    test_long_starts_on_the_organiser_maps()
    test_macro_maps_start_one_long_dragon()
    test_diagonal_walls_on_the_organiser_maps()
    test_curves_maps_have_diagonal_walls()
    test_maze_is_only_portal_labyrinth_variants()
    test_sprint_is_only_sprint_variants()
    test_nearportal_is_only_portal_ladder_variants()
    test_near_keeps_sealed_start_rooms()
    test_near_keeps_portal_pockets()
    test_sized_styles_keep_to_their_size()
    test_generate_all_falls_back_to_one_by_one()
    test_worldgen_maps_are_pinned_mirrored_and_ours()
    test_worldgen_never_writes_a_body_across_a_portal()
    print("ok")
