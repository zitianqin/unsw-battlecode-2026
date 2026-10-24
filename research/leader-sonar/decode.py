"""Decoders for the leaders' sonar formats found in research/leader-sonar/README.md (study of 2026-09-24).

    from decode import sss, sabotage
    sss(0xb3c026823eca5ae1)   # {'id': 154, 'length': 4, 'x': 31, 'y': 25, 'round': 150, 'check': ...}

Each returns None for a value that doesn't carry the team's marker. The check bits are kept but not verified:
their functions weren't recovered.
"""

from __future__ import annotations

SSS_MAGIC = 0xB3C


# SSS (team 91), 64-bit: magic 12 | dragon id 14 | length 7 | x 6 | y 6 | round 9 | check 10 (high to low).
def sss(value: int) -> dict | None:
    if value >> 52 != SSS_MAGIC:
        return None
    return {"id": (value >> 38) & 0x3FFF, "length": (value >> 31) & 0x7F, "x": (value >> 25) & 0x3F,
            "y": (value >> 19) & 0x3F, "round": (value >> 10) & 0x1FF, "check": value & 0x3FF}


# Sabotage-d (team 46), 32-bit: x 6 | y 6 | length 8 | flags 4 | low 8 (probably a check). Flag bit 0 (value bit
# 8) marks a sighting of an enemy of theirs; flag bits 1-2 count relay hops, about a round of lag each.
def sabotage(value: int) -> dict | None:
    if value >> 32:
        return None
    flags = (value >> 8) & 0xF
    return {"x": (value >> 26) & 0x3F, "y": (value >> 20) & 0x3F, "length": (value >> 12) & 0xFF,
            "enemy_sighting": bool(flags & 1), "hops": (flags >> 1) & 0x3, "low": value & 0xFF}


if __name__ == "__main__":
    print(sss(0xB3C026823ECA5AE1))  # id 154, len 4, (31, 25), round 150: matches game 137771
    print(sabotage(0x8A020422))     # (34, 32), len 32, 2 relay hops: dragon 209 in game 126934, round 350
