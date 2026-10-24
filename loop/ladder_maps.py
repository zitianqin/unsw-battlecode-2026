"""The ladder's active map pool, read from the server hourly by the upkeep (`refresh`, from maintenance.tick for both
providers) into <loop home>/ladder-maps.json: each active map's id, name, size, three sha256s, of its raw mapText
(`sha256`), of its content (`content`, content_hash) and of its layout without pearl timings (`layout`, layout_hash),
and `gapless`, whether its text gives no tile a pearl spawn. Until 2026-09-26 nothing recorded it, and the pool grew
from 9 maps to 11 (Prisoners Dilemma and Autarky) unnoticed.

From 2026-09-27 21:00 AEST (rtrgrd, #announcements) the site may withhold pearl timings from map text, add variants of
a map (same kelp and portals, other pearl timings) and change a map's pearls drastically. So `differences` tells a
pearl-only change ("pearls changed") and a variant beside a map we still match ("variant", committed as its own file)
from other changes, and never offers gapless text for committing ("withheld").

Our copies are the maps in LOCAL (maps/ladder, then maps/sprint), matched to the server's by content, never by file
name. health.py alerts LADDER MAPS CHANGED when the two differ (`differences`), so the orchestrator adds or retires
files there. The saved list drives only that alert: the official-map report and the CPU gate play every file in LOCAL
of the referee checkout they run against (`local_files`; referee/cpu.py's DEFAULT_MAPS adds maps/retired, the maps
the ladder dropped), which changes only when a map is committed to main and reaches ~/.bc-loop/referee
(driver.refresh_referee), never mid-test.

Each active map's mapText is also kept, once per content, as <loop home>/ladder-map-files/<content>.map (FILES; at most
one file per map version the server has ever listed, tens of files of under 100 KB), so the map-ideas worker
(loop/mapideas.py) can study a map the pool gains before anyone has committed it to maps/.

Pearl variants no endpoint lists (organisers, 2026-09-27; GET /maps gave one mapText a map on 2026-09-28): from
2026-09-28 the site plays some games of a map on other pearl timings under the same id (10774's saved replays: 98 of
433, on Queen Of Spades, Schooltime, Devil, Slithery Fight and a 10-dragon Prisoners Dilemma), and replays carry no
timings (TILE lines `x y 0 0`, no countdowns). A replay does carry its seed and every pearl that spawned, and the
engine's schedule is a pure function of the two: one std::mt19937_64 seeded with the replay's seed, each tile owning
a countdown (the first of a mirrored pair in row-major order) drawing min + draw % (max - min + 1), all owners in
row-major order at the start and each owner reaching 0 in a round in row-major order (pearl_attempts; matched every
spawn of three base-map games on Devil, Queen Of Spades and Default, 511 on one). So `rebuild_variant` fits each
owner's range to its first spawn across a variant's games, and loop/online_fit.py keeps a rebuilt map in VARIANTS
(<loop home>/ladder-map-variants/<content>.map, one file a variant, under 110 KB) once the engine plays every game it
was fitted on again; tools/why.py tries those files beside the others.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections.abc import Callable
from pathlib import Path

RECORD = "ladder-maps.json"
FILES = "ladder-map-files"
VARIANTS = "ladder-map-variants"
EVERY = 3600
LOCAL = ("maps/ladder", "maps/sprint")


# sha256 of a map's content: line endings made \n, trailing whitespace dropped from each line and the end, and the
# MAP_NAME line dropped, since the engine only displays the name and our files spell some differently
# (maps/retired/stronghold.map says "stronghold", the server "Stronghold"). Checked 2026-09-26: with line endings and
# MAP_NAME ignored, all 11 active maps equal a local file byte for byte; the whitespace rule is only a safeguard.
def content_hash(text: str) -> str:
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
             if not line.startswith("MAP_NAME ")]
    return hashlib.sha256("\n".join(lines).rstrip().encode()).hexdigest()


# sha256 of a map's layout: its content (content_hash) without the TILE_COUNT and TILE lines, so a map and a variant of
# it with the same size, kelp, portals and dragons but other pearl timings share it.
def layout_hash(text: str) -> str:
    return content_hash("\n".join(line for line in text.splitlines()
                                  if line.split()[:1] not in (["TILE"], ["TILE_COUNT"])))


# A map's layout (layout_hash) with every DRAGON line's team left out: from 2026-09-28 ~02:00 UTC the site plays each
# map both as its file has it and with the teams' labels swapped on the same bodies (DRAGON 0 ... read DRAGON 1 ...),
# in the same file order, pearl timings unchanged (MISSES 72), so either matches the file (tools/why.py).
def teamless_layout(text: str) -> str:
    return layout_hash("\n".join(f"DRAGON ? {' '.join(f[2:])}" if (f := line.split())[:1] == ["DRAGON"] else line
                                 for line in text.splitlines()))


# Where `team` ("A", the map's DRAGON 0 lines, or "B", its DRAGON 1 lines) starts on a map, as 16 hex digits: its
# teamless_layout and each of that team's DRAGON lines with its place among them (a dragon's place is its id, so its
# turn order too). Two games start us alike exactly when they share it, whichever label we had: on a map the site
# played with the labels swapped our side B starts where side A does on the file's (online.start_key).
def start_hash(text: str, team: str) -> str:
    label = "0" if team == "A" else "1"
    dragons = [f.split() for f in text.splitlines() if f.split()[:1] == ["DRAGON"]]
    ours = [f"{place} {' '.join(f[2:])}" for place, f in enumerate(dragons) if f[1] == label]
    return hashlib.sha256("\n".join([teamless_layout(text), *ours]).encode()).hexdigest()[:16]


# Whether map text gives no tile a pearl spawn (no TILE line with two whole-number gaps and a max gap above 0): from
# 2026-09-27 21:00 AEST the site may withhold pearl timings (TILE lines removed, cut to `TILE x y`, or not numbers;
# its replays did so from ~21:10 with every TILE line `x y 0 0`, while GET /maps still gave real gaps at 21:34).
# Such text has the real layout but no pearls; committed as a map it would be played without any, so never commit it.
def gapless(text: str) -> bool:
    for line in text.splitlines():
        f = line.split()
        if len(f) >= 5 and f[0] == "TILE" and f[3].isdecimal() and f[4].isdecimal() and int(f[4]) > 0:
            return False
    return True


# A map name or file stem reduced to letters and digits, so "Default Small", default_small and "default small" meet.
def name_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def read(state: Path) -> dict:
    try:
        value = json.loads((state / RECORD).read_text())
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def save(state: Path, record: dict) -> None:
    temporary = state / f"{RECORD}.tmp"
    temporary.write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n")
    temporary.replace(state / RECORD)


# The saved entry for each active map in a GET /maps response (a list, or an object wrapping one).
def active_maps(payload: object) -> list[dict]:
    import ladder
    return [{"id": m.get("id"), "name": m.get("name"), "size": m.get("sizeLabel"),
             "sha256": hashlib.sha256(m["mapText"].encode()).hexdigest(), "content": content_hash(m["mapText"]),
             "layout": layout_hash(m["mapText"]), "gapless": gapless(m["mapText"])}
            for m in ladder.items(payload, "maps") if m.get("active")]


# The upkeep's hourly read of GET /maps into ladder-maps.json: `fetched_at` and `maps` on success, `attempt_at` every
# try and `error` for a failure, which keeps the last good `maps` (health.UPKEEP_JOBS alerts after 3 h without one).
def refresh(state: Path, now: float | None = None, fetch: Callable[[str], object] | None = None) -> None:
    import ladder
    from meta import locked
    now = time.time() if now is None else now
    with locked(state / "ladder-maps"):
        record = read(state)
        if now - record.get("attempt_at", 0) < EVERY:
            return
        record["attempt_at"] = now
        save(state, record)
        try:
            payload = (fetch or ladder.get)("/maps")
            maps = active_maps(payload)
            if not maps:
                raise ValueError("GET /maps listed no active map")
            keep_files(state, payload)
            record.update(maps=maps, fetched_at=now, error=None)
        except (Exception, SystemExit) as error:  # ladder.api_key exits without a key
            record["error"] = f"{type(error).__name__}: {error}"[:600]
        save(state, record)


# Writes each active map's mapText in a GET /maps response to FILES as <content>.map, unless already there: one file per
# content, so same-name variants are kept apart, and gapless text is kept too (its layout is real; map-ideas skips it).
def keep_files(state: Path, payload: object) -> None:
    import ladder
    folder = state / FILES
    folder.mkdir(exist_ok=True)
    for m in ladder.items(payload, "maps"):
        if m.get("active") and not (path := map_file(state, content_hash(m["mapText"]))).exists():
            temporary = path.with_suffix(".tmp")
            temporary.write_text(m["mapText"])
            temporary.replace(path)


# Where keep_files keeps the map whose content hash is `content`.
def map_file(state: Path, content: str) -> Path:
    return state / FILES / f"{content}.map"


# Every .map file in `root`'s LOCAL folders, in order: maps/ladder's sorted, then maps/sprint's. The official-map
# report's maps (driver.official_maps); referee/cpu.py's DEFAULT_MAPS, which can't import this, adds maps/retired.
def local_files(root: Path) -> list[Path]:
    return [path for folder in LOCAL for path in sorted((root / folder).glob("*.map"))]


# The names a local map answers to: its file stem and its MAP_NAME, as name_keys.
def local_names(path: Path, text: str) -> set[str]:
    named = [line[len("MAP_NAME "):] for line in text.splitlines() if line.startswith("MAP_NAME ")]
    return {name_key(path.stem), *(name_key(name) for name in named[:1])}


# How the saved active pool differs from `root`'s local copies, by content: each a short phrase naming the map.
# - `new`: an active map no local file matches in content, layout or name.
# - `pearls changed`: one whose layout a maps/ladder file has, with other pearl timings, and no other active map
#   matches that file: commit it over that file.
# - `changed`: one whose name a maps/ladder file has but not its layout or content, likewise.
# - `variant`: one whose layout (or else name) a local file has that another active map still matches exactly, or
#   that only a maps/sprint file has (the toolkit's Sprint maps, never overwritten): committed as its own file with a
#   distinct stem.
# - `withheld`: a gapless one (the site withheld its pearl timings), never to be committed; its local file stays.
# - `gone`: local maps/ladder files (the report and the CPU gate still play them) no active map matches or stands for.
# - `gapless`: local files whose text gives no tile a pearl spawn, which the report and the CPU gate would play
#   without pearls; checked with or without a saved pool.
# Layouts are compared only for maps saved with one (ladder-maps.json rows from before 2026-09-27 have none).
def differences(record: dict, root: Path) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {kind: [] for kind in ("new", "gone", "changed", "pearls changed", "variant",
                                                         "withheld", "gapless")}
    local = [(path, content_hash(text), layout_hash(text), local_names(path, text), gapless(text))
             for path in local_files(root) for text in [path.read_text()]]
    found["gapless"] = [str(path.relative_to(root)) for path, *_, empty in local if empty]
    maps = record.get("maps") or []
    if not maps:
        return found
    hashes = {h for _, h, *_ in local}
    active = {m["content"]: m for m in maps}
    content = {path: h for path, h, *_ in local}
    files = lambda paths: ", ".join(str(p.relative_to(root)) for p in paths)  # noqa: E731
    kept = set()  # local files an active map stands for without matching them exactly
    for m in maps:
        if m["content"] in hashes:
            continue
        label = f"{m['name']} (id {m['id']}, {m.get('size')})"
        shaped = [path for path, _, layout, _, _ in local if m.get("layout") and layout == m["layout"]]
        named = [path for path, _, _, names, _ in local if name_key(m["name"] or "") in names]
        same = shaped or named
        # maps/sprint's files are the toolkit's Sprint maps (mapgen's sprint style and the CPU gate play them): never
        # committed over, so a changed Sprint map is a variant for maps/ladder.
        free = [path for path in same if content[path] not in active and path.parent.name != "sprint"]
        if m.get("gapless"):
            kept.update(same)
            where = f"layout matches {files(shaped)}" if shaped else "layout matches no local file"
            found["withheld"].append(f"{label}: pearl timings withheld, {where}")
        elif not same:
            found["new"].append(label)
        elif not free and (sprint := [p for p in same if content[p] not in active]):
            found["variant"].append(f"{label}: same {'layout' if shaped else 'name'} as {files(sprint)}, a toolkit "
                                    "Sprint map kept as it is: commit it as its own file under maps/ladder")
        elif not free:
            twins = ", ".join(f"{files([p])} (id {active[content[p]]['id']})" for p in same)
            found["variant"].append(f"{label}: same {'layout' if shaped else 'name'} as {twins}, which still match")
        else:
            kept.update(free)
            kind, what = ("pearls changed", "pearl timings differ") if shaped else ("changed", "differs")
            found[kind].append(f"{label}: {files(free)} {what}")
    # maps/sprint stays played when the ladder drops a Sprint-like map (the Sprint's own maps; mapgen's `sprint` style).
    found["gone"] = [str(path.relative_to(root)) for path, h, *_ in local
                     if h not in active and path not in kept and path.parent.name == "ladder"]
    return found


# ------------------------------------------------------------------------ pearl variants (rebuilt from replays)

# std::mt19937_64, the engine's pearl generator (pearl_attempts).
class _MT64:
    def __init__(self, seed: int) -> None:
        self.state = [seed & (2 ** 64 - 1)]
        for i in range(1, 312):
            self.state.append((6364136223846793005 * (self.state[-1] ^ (self.state[-1] >> 62)) + i) & (2 ** 64 - 1))
        self.at = 312

    def __call__(self) -> int:
        if self.at >= 312:
            mt = self.state
            for k in range(312):
                y = (mt[k] & 0xFFFFFFFF80000000) | (mt[(k + 1) % 312] & 0x7FFFFFFF)
                mt[k] = mt[(k + 156) % 312] ^ (y >> 1) ^ (0xB5026F5AA96619E9 if y & 1 else 0)
            self.at = 0
        x = self.state[self.at]
        self.at += 1
        x ^= (x >> 29) & 0x5555555555555555
        x ^= (x << 17) & 0x71D67FFFEDA60000
        x ^= (x << 37) & 0xFFF7EEE000000000
        return x ^ (x >> 43)


def _row_major(tile: tuple[int, int]) -> tuple[int, int]:
    return tile[1], tile[0]


# A map text's size, its mirror (SYMMETRY: x mirrors y, y mirrors x, xy both, none itself) and TILE ranges.
def pearl_tiles(text: str) -> tuple[int, int, Callable, dict[tuple[int, int], tuple[int, int]]]:
    lines = text.splitlines()
    width, height = map(int, lines[0].split()[1:3])
    symmetry = next((line.split()[1] for line in lines if line.startswith("SYMMETRY ")), None)
    flip = {"x": lambda x, y: (x, height - 1 - y), "y": lambda x, y: (width - 1 - x, y),
            "xy": lambda x, y: (width - 1 - x, height - 1 - y)}.get(symmetry, lambda x, y: (x, y))
    ranges = {(int(f[1]), int(f[2])): (int(f[3]), int(f[4])) for line in lines
              if len(f := line.split()) >= 5 and f[0] == "TILE" and f[3].isdecimal() and f[4].isdecimal()}
    return width, height, flip, ranges


# The tile owning `tile`'s countdown: the first of it and its mirror in row-major order.
def owner(tile: tuple[int, int], flip: Callable) -> tuple[int, int]:
    return min(tile, flip(*tile), key=_row_major)


# The rounds each owner attempts a spawn in games on `ranges` ({owner: (min, max)}, owners only) with `seed`, the
# engine's schedule (the module docstring): {owner: {round, ...}}.
def pearl_attempts(ranges: dict[tuple[int, int], tuple[int, int]], seed: int, rounds: int = 500) -> dict:
    draw, order = _MT64(seed), sorted(ranges, key=_row_major)
    left = {o: ranges[o][0] + draw() % (ranges[o][1] - ranges[o][0] + 1) for o in order}
    found: dict = {o: set() for o in order}
    for r in range(rounds):
        for o in order:
            left[o] -= 1
            if left[o] == 0:
                found[o].add(r)
                left[o] = ranges[o][0] + draw() % (ranges[o][1] - ranges[o][0] + 1)
    return found


# The (min, max) best explaining an owner's first spawns: `seen` is [(its initial draw, its first spawn's round or None,
# the game's last round)] a game. A first attempt comes at min + draw % span - 1 and spawns unless the tile is taken,
# so a first spawn earlier than it rules a range out, one at it scores, and none in a game that reached it counts
# against. Ties go to a range in `known` (the base map's), then the larger span (a shorter game can't tell 500 from
# 1000). None when no range fits.
FIT_MIN = 60  # the largest min tried (the ladder's maps use 1-20 on 2026-09-29)
FIT_SPAN = 1200  # the largest span tried (their largest max is 1000)


def fit_range(seen: list[tuple[int, int | None, int]], known: tuple = ()) -> tuple[int, tuple[int, int]] | None:
    best = None
    for low in range(1, FIT_MIN + 1):
        for span in range(1, FIT_SPAN + 1):
            score = 0
            for draw, first, last in seen:
                attempt = low + draw % span - 1
                if first is None:
                    score -= attempt <= last
                elif first < attempt:
                    break
                else:
                    score += first == attempt
            else:
                key = (score, (low, low + span - 1) in known, span)
                if best is None or key > best[0]:
                    best = (key, (low, low + span - 1))
    return (best[0][0], best[1]) if best else None


# A variant's TILE ranges from `games` played on it, [(seed, {tile: [rounds it spawned in]}, last round)], and the base
# map's text (the same layout on the timings we have: its owners stand in for those no game shows spawning, since a
# variant keeps most of them; each is kept only when counting it aligns the next spawning owner's draws better):
# {tile: (min, max)} for every spawning tile and its mirror, from `text`'s symmetry. The caller checks the result
# with the engine: a game whose owners all spawned late, or a variant that changed its symmetry (Slithery Fight's
# on 2026-09-29), fits wrong and plays differently.
def rebuild_variant(games: list[tuple[int, dict, int]], text: str, base: str) -> dict[tuple[int, int], tuple[int, int]]:
    _, _, flip, _ = pearl_tiles(text)
    base_ranges = {t: r for t, r in pearl_tiles(base)[3].items() if r[1] > 0 and owner(t, flip) == t}
    firsts = []
    for _, spawned, _ in games:
        first: dict = {}
        for tile, rounds in spawned.items():
            o = owner(tile, flip)
            first[o] = min(first.get(o, math.inf), min(rounds))
        firsts.append(first)
    seen = set().union(*firsts)
    candidates = sorted(seen | set(base_ranges), key=_row_major)
    draws = []
    for seed, _, _ in games:
        draw = _MT64(seed)
        draws.append([draw() for _ in candidates])
    ranges, at, silent = {}, 0, []
    for o in candidates:
        if o not in seen:
            silent.append(o)
            continue
        known = (base_ranges[o],) if o in base_ranges else ()
        options = []
        for count in range(len(silent) + 1):
            if at + count < len(candidates) and (fit := fit_range(
                    [(draws[g][at + count], firsts[g].get(o), games[g][2]) for g in range(len(games))], known)):
                options.append((fit[0], -count, count, fit[1]))
        if not options:
            raise ValueError(f"no pearl range fits the owner at {o}")
        _, _, count, fitted = max(options)
        ranges.update((s, base_ranges[s]) for s in silent[:count])
        at, silent = at + count + 1, []
        ranges[o] = fitted
    return {**ranges, **{flip(*o): r for o, r in ranges.items()}}


# `text` (a replay's map text, TILE lines without timings) with `ranges` ({tile: (min, max)}, others 0 0).
def with_ranges(text: str, ranges: dict[tuple[int, int], tuple[int, int]]) -> str:
    out = []
    for line in text.splitlines():
        f = line.split()
        if f[:1] == ["TILE"] and len(f) >= 3:
            low, high = ranges.get((int(f[1]), int(f[2])), (0, 0))
            line = f"TILE {f[1]} {f[2]} {low} {high}"
        out.append(line)
    return "\n".join(out) + "\n"
