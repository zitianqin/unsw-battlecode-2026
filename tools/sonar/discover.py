"""Finds a team's plain sonar layouts on its replays, as spec entries (tools/sonar/spec.toml) for verify.py to judge.

The field search, per team, over a sample of its values beside the true heads of its dragons (and of its enemy's, for
sightings) in the rounds around each send:
  1. position: the 6-bit fields most often equal to a head's x and y, tried in pairs, or a 12-bit y * W + x tile;
  2. with the position fixed, the length (5-9 bits), round (9-10 bits, within a round) and id (6-16 bits) fields that
     match the head found, each at PER_FIELD or more;
  3. the recogniser: bits constant (0.5% either way) over the values the position matched, then more bits, each kept
     by nearly every true report and dropped by false ones, until the false ones are 2% or less;
  4. the values that layout doesn't recognise are searched again, up to three layouts.
Besides, the bits constant in 99% of all its values make a "-tag" recogniser (kind keyed) naming the team.

    .venv/bin/python tools/sonar/discover.py --team 91 [--since ISO] [--games 15] [--version replays-2026-10-10]
"""

from __future__ import annotations

import argparse
import collections
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.append(str(HERE))

import formats  # noqa: E402
import games as G  # noqa: E402

PER = 120  # values sampled per game
ROUNDS = range(-6, 2)  # rounds around a send whose heads a report may name
MIN_RATE = 0.3  # a position search below this finds nothing
PER_FIELD = 0.6
TAG_SHARE = 0.99


class Row:
    """One sampled value with the heads it may name: xy maps a head's (x, y) to its (length, id, round)s."""

    __slots__ = ("v", "W", "H", "xs", "ys", "xy", "tiles")

    def __init__(self, v: int, W: int, H: int, heads: list[tuple[int, int, int, int, int]]) -> None:
        self.v, self.W, self.H = v, W, H
        self.xs = {h[0] for h in heads}
        self.ys = {h[1] for h in heads}
        self.xy = collections.defaultdict(list)
        for x, y, length, did, rnd in heads:
            self.xy[(x, y)].append((length, did, rnd))
        self.tiles = {y * W + x for x, y, *_ in heads}


def bits(v: int, shift: int, width: int) -> int:
    return (v >> shift) & ((1 << width) - 1)


# The sampled rows of `team`'s games ([(game, side)]): its own heads (sighting False) or its enemy's.
def rows_of(played: list[tuple[dict, int]], sighting: bool, seed: int = 1) -> list[Row]:
    rng = random.Random(seed)
    rows = []
    for game, side in played:
        st, p = game["states"], game["pings"]
        who = 1 - side if sighting else side
        heads = collections.defaultdict(list)
        for i in range(len(st["r"])):
            if st["t"][i] == who:
                heads[st["r"][i]].append((st["x"][i], st["y"][i], st["len"][i], st["id"][i], st["r"][i]))
        idx = G.sent_by(game, side)
        for i in sorted(rng.sample(idx, min(PER, len(idx)))):
            r = p["r"][i]
            rows.append(Row(p["v"][i], game["W"], game["H"], [h for d in ROUNDS for h in heads.get(r + d, [])]))
    return rows


def position(row: Row, enc: tuple) -> tuple[int, int]:
    if enc[0] == "xy":
        return bits(row.v, enc[1], 6), bits(row.v, enc[2], 6)
    t = bits(row.v, enc[1], 12)
    return t % row.W, t // row.W


def span(shift: int, width: int) -> int:
    return ((1 << width) - 1) << shift


def encoding_span(enc: tuple) -> int:
    return span(enc[1], 6) | span(enc[2], 6) if enc[0] == "xy" else span(enc[1], 12)


# The position encoding that names a head most often: (rate, ("xy", x shift, y shift) or ("tile", shift)).
def locate(rows: list[Row]) -> tuple[float, tuple | None]:
    n = len(rows)
    tops = []
    for axis in ("xs", "ys"):
        scored = sorted(((sum(bits(r.v, s, 6) in getattr(r, axis) for r in rows), s) for s in range(59)), reverse=True)
        tops.append([s for _, s in scored[:6]])
    best: tuple[float, tuple | None] = (0.0, None)
    for sx in tops[0]:
        for sy in tops[1]:
            if abs(sx - sy) >= 6:
                rate = sum((bits(r.v, sx, 6), bits(r.v, sy, 6)) in r.xy for r in rows) / n
                best = max(best, (rate, ("xy", sx, sy)), key=lambda b: b[0])
    for s in range(53):
        rate = sum(bits(r.v, s, 12) in r.tiles for r in rows) / n
        best = max(best, (rate, ("tile", s)), key=lambda b: b[0])
    return best


# The length, round and id fields of the rows whose position `enc` matched: {name: (shift, width)}.
def fields(rows: list[Row], enc: tuple) -> dict[str, tuple[int, int]]:
    matched = [(r, r.xy[position(r, enc)]) for r in rows if position(r, enc) in r.xy]
    taken = encoding_span(enc)
    found: dict[str, tuple[int, int]] = {}
    if len(matched) < 10:
        return found
    # Widths are tried narrowest first and a field must equal the head's value whole, so zero bits above a field
    # don't widen it; a round is within a round of the head's, modulo its width.
    for name, widths in (("length", range(5, 10)), ("round", (9, 10)), ("id", (6, 7, 8, 9, 10, 11, 12, 13, 14, 16))):
        best = (0.0, None)
        for w in widths:
            m = (1 << w) - 1
            for s in range(65 - w):
                if span(s, w) & taken:
                    continue
                if name == "round":
                    hit = sum(any((bits(r.v, s, w) - h[2] + 1) & m <= 2 for h in hs) for r, hs in matched)
                else:
                    k = 0 if name == "length" else 1
                    hit = sum(any(bits(r.v, s, w) == h[k] for h in hs) for r, hs in matched)
                best = max(best, (hit / len(matched), (s, w)), key=lambda b: b[0])
        if best[0] >= PER_FIELD and best[1]:
            found[name] = best[1]
            taken |= span(*best[1])
    return found


# The bits of `values` equal in all but `slack` of them: (mask, value).
def constant_bits(values: list[int], slack: float) -> tuple[int, int]:
    mask = value = 0
    for b in range(64):
        ones = sum(v >> b & 1 for v in values) / len(values)
        if ones >= 1 - slack or ones <= slack:
            mask |= 1 << b
            value |= (ones >= 1 - slack) << b
    return mask, value


# Whether `row`'s value, read by `layout`, names a head (with its round and length, when the layout has them).
def true_report(row: Row, enc: tuple, found: dict) -> bool:
    for length, _, rnd in row.xy.get(position(row, enc), []):
        if "round" in found and (bits(row.v, *found["round"]) - rnd + 1) & ((1 << found["round"][1]) - 1) > 2:
            continue
        if "length" in found and abs(bits(row.v, *found["length"]) - length) > 1:
            continue
        return True
    return False


# Adds recogniser bits, each kept by 97% of the true reports, that drop the most false ones, until 2% are left.
def tighten(rows: list[Row], enc: tuple, found: dict, mask: int, value: int) -> tuple[int, int]:
    free = [b for b in range(64) if not ((mask | encoding_span(enc) | sum(span(*f) for f in found.values())) >> b & 1)]
    for _ in range(8):
        rec = [r for r in rows if r.v & mask == value]
        good = [r for r in rec if true_report(r, enc, found)]
        bad = [r for r in rec if not true_report(r, enc, found)]
        if not good or len(bad) <= 0.02 * len(rec):
            break
        best = None
        for b in free:
            ones = sum(r.v >> b & 1 for r in good) / len(good)
            want = int(ones >= 0.5)
            if max(ones, 1 - ones) < 0.97:
                continue
            dropped = sum((r.v >> b & 1) != want for r in bad)
            if best is None or dropped > best[0]:
                best = (dropped, b, want)
        if not best or best[0] <= 0.05 * len(bad):
            break
        mask, value = mask | 1 << best[1], value | best[2] << best[1]
        free.remove(best[1])
    return mask, value


# Up to three layouts found in `rows` (each searched on the values the previous ones don't recognise).
def layouts(rows: list[Row], sighting: bool = False) -> list[dict]:
    out, rest = [], rows
    for _ in range(3):
        if len(rest) < 40:
            break
        rate, enc = locate(rest)
        if enc is None or rate < MIN_RATE:
            break
        found = fields(rest, enc)
        mask, value = constant_bits([r.v for r in rest if position(r, enc) in r.xy], 0.005)
        mask &= ~(encoding_span(enc) | sum(span(*f) for f in found.values()))
        value &= mask
        mask, value = tighten(rest, enc, found, mask, value)
        layout = dict(mask=hex(mask), value=hex(value))
        if enc[0] == "xy":
            layout.update(x=(enc[1], 6), y=(enc[2], 6))
        else:
            layout.update(tile=(enc[1], 12))
        layout.update(found)
        if sighting:
            layout["sighting"] = True
        out.append(layout)
        rest = [r for r in rest if r.v & mask != value]
    return out


# The team's tag: bits constant in TAG_SHARE of its values, when there are eight or more and not all 64.
def tag(values: list[int]) -> dict | None:
    if len(values) < 200:
        return None
    mask, value = constant_bits(values, 1 - TAG_SHARE)
    if bin(mask).count("1") < 8 or mask == (1 << 64) - 1:
        return None
    return dict(mask=hex(mask), value=hex(value))


# The spec entries discovery proposes for `team` from `played` ([(game, side)]): a report entry with the layouts it
# found (its own heads first, then sightings), else a keyed stub, and a "-tag" entry.
def entries(team: int, name: str, played: list[tuple[dict, int]], version: str) -> list[dict]:
    values = [game["pings"]["v"][i] for game, side in played for i in G.sent_by(game, side)]
    if not values:
        return [dict(team=team, name=name, version=version, kind="silent",
                     source=f"replays: no pings in {len(played)} ranked games")]
    first, last = min(g["meta"]["at"] for g, _ in played)[:16], max(g["meta"]["at"] for g, _ in played)[:16]
    source = f"replays: tools/sonar/discover.py over {len(played)} ranked games from {first} to {last}"
    found = (layouts(rows_of(played, False)) + layouts(rows_of(played, True), sighting=True))[:3]
    out = [dict(team=team, name=name, version=version, kind="report" if found else "keyed", source=source,
                note="found by the field search" if found else "no plain field found", layout=found)]
    if (t := tag(values[:: max(1, len(values) // 20000)])) is not None:
        out.append(dict(team=team, name=name, version=f"{version}-tag", kind="keyed", source=source,
                        note="bits constant in 99% of its values", layout=[t]))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--team", type=int, required=True)
    parser.add_argument("--name", default=None, help="the team's name for the entry (default: from the spec)")
    parser.add_argument("--since", help="only games from this ISO time (UTC)")
    parser.add_argument("--games", type=int, default=15, help="newest games to search")
    parser.add_argument("--version", default="replays-new", help="the entries' version label")
    parser.add_argument("--cache", type=Path, default=G.CACHE)
    parser.add_argument("--append", action="store_true", help="append the entries to tools/sonar/spec.toml")
    args = parser.parse_args()
    rows = G.team_games(G.games(), args.since).get(args.team, [])[: args.games]
    played = [(G.extract(meta, path, args.cache), side) for meta, path, side in rows]
    name = args.name or next((f["name"] for f in formats.load() if f["team"] == args.team), f"team {args.team}")
    found = entries(args.team, name, played, args.version)
    if args.append:
        formats.append(found)
    print("\n".join(formats.entry_toml(e) for e in found))
    return 0


if __name__ == "__main__":
    sys.exit(main())
