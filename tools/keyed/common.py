"""Shared paths and loaders for the keyed-sonar tools (tools/keyed/README.md).

Everything these tools write lives in DATA (~/.bc-loop/keyed, or $KEYED_DIR), never in git: fetched replays
(DATA/replays with its index.jsonl), the combined index (DATA/games.jsonl), per-team pickles (DATA/pings/<team>.pkl)
and battery results (DATA/battery/<team>.json).
"""
from __future__ import annotations

import calendar
import json
import os
import pickle
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'loop'))
import home  # noqa: E402

HOME = home.loop_home()
DATA = Path(os.environ.get('KEYED_DIR', HOME / 'keyed'))
# Replay folders with an index.jsonl (tools/replays.py's format), read in this order; the first copy of a match wins.
SOURCES = [('keyed', DATA / 'replays'), ('data', ROOT / 'data' / 'replays'),
           ('data-main', Path('/Users/me/Dev/unsw-battlecode-2026/data/replays')),
           ('study', HOME / 'study-replays'), ('ladder', HOME / 'ladder-replays')]


# A game row's time (its requestedAt, else fetchedAt) as epoch seconds; 0 when missing.
def when(meta: dict) -> float:
    at = meta.get('at') or ''
    try:
        return calendar.timegm(time.strptime(at[:19], '%Y-%m-%dT%H:%M:%S'))
    except ValueError:
        return 0.0


# epoch seconds of an ISO time such as 2026-10-01T06:00 (UTC) or with +10:00
def iso(t: str) -> float:
    from datetime import datetime, timezone
    d = datetime.fromisoformat(t.replace('Z', '+00:00'))
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()


# One team's extracted games {match: game} (extract.py), ranked only, optionally from `since` (ISO) on.
def load(team: int, since: str | None = None) -> dict:
    D = pickle.load(open(DATA / 'pings' / f'{team}.pkl', 'rb'))
    t0 = iso(since) if since else 0
    return {m: g for m, g in D.items() if g['meta'].get('ranked') and when(g['meta']) >= t0}


# The first ray of each (round, sender) turn of a game, in order: one row per sending turn.
def turns(g: dict) -> list[dict]:
    seen = set(); out = []
    for p in g['pings']:
        k = (p['round'], p['sender'])
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out


def games_index() -> list[dict]:
    return [json.loads(l) for l in open(DATA / 'games.jsonl')]
