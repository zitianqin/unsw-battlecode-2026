"""The replays the sonar tools read, newest first, and each game's extraction (every ping and every dragon's true head
and length at its turn start), cached one file per game.

    rows = games()                       # [(meta, replay path)], meta: match, ranked, A, B, at, sA, sB, winner
    game = extract(meta, path, cache)    # {"meta", "W", "H", "map", "pings": {...}, "states": {...}}

A game's pings are columns r (round), t (sender side, 0 = A), v (value), s (sender id), hit (hit dragon id, -1 none), x,
y, len (the sender's head after its move and its length); its states r, id, t, x, y, len. A cached file is about 0.2 MB
and rebuilt from the replay when missing or older than this layout.
"""

from __future__ import annotations

import glob
import json
import os
import pickle
import sys
import tempfile
import zlib
from array import array
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.append(str(REPO / "loop"))  # after tools/sonar, whose reports.py loop/ also names
import home  # noqa: E402

LOOP_HOME = home.loop_home()
INDEXES = [REPO / "data/replays/index.jsonl", LOOP_HOME / "study-replays/index.jsonl",
           LOOP_HOME / "scout-replays/index.jsonl"]
CACHE = Path(tempfile.gettempdir()) / "bc-sonar-cache"
OURS = 157
PING_COLUMNS = dict(r="h", t="b", v="Q", s="i", hit="i", x="h", y="h", len="h")
STATE_COLUMNS = dict(r="h", id="i", t="b", x="h", y="h", len="h")


# Every replay with its index row, newest first: the fetch index (tools/replays.py fetch writes data/replays), the
# loop's study and scout replays, our own ranked online games, and any `extra` index.jsonl files.
def games(extra: tuple[Path, ...] = ()) -> list[tuple[dict, str]]:
    rows: dict[int, tuple[dict, str]] = {}
    for index in [*INDEXES, *extra]:
        if not index.exists():
            continue
        for line in index.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            path = index.parent / f"{r['match']}.replay"
            if path.exists() and r["match"] not in rows:
                rows[r["match"]] = (dict(match=r["match"], ranked=bool(r.get("ranked")), A=r["teamAId"], B=r["teamBId"],
                                         at=r.get("requestedAt") or r.get("fetchedAt") or "", sA=r.get("submissionAId"),
                                         sB=r.get("submissionBId"), winner=r.get("winner")), str(path))
    for index in glob.glob(str(LOOP_HOME / "online-replays/*/index.json")):
        try:
            listed = json.loads(Path(index).read_text())["games"]
        except (OSError, ValueError, KeyError):
            continue
        for g in listed:
            if not g.get("ranked") or g.get("id") in rows or not g.get("replay") or not os.path.exists(g["replay"]):
                continue
            ours_a = g.get("side") == "A"
            winner = (g.get("summary") or {}).get("result", {}).get("winner")
            rows[g["id"]] = (dict(match=g["id"], ranked=True, A=OURS if ours_a else g.get("opponent"),
                                  B=g.get("opponent") if ours_a else OURS, at=g.get("requested_at") or "",
                                  sA=g.get("submission_id") if ours_a else g.get("opponent_submission_id"),
                                  sB=g.get("opponent_submission_id") if ours_a else g.get("submission_id"),
                                  winner=winner.lower() if winner else None), g["replay"])
    return sorted(rows.values(), key=lambda item: item[0]["at"], reverse=True)


# The game's extraction, from the cache or by walking the replay with tools/replays.py (which must read value64:
# main's does).
def extract(meta: dict, path: str, cache: Path = CACHE) -> dict:
    out = cache / f"{meta['match']}.pkl"
    if out.exists():
        game = pickle.loads(zlib.decompress(out.read_bytes()))
        if set(PING_COLUMNS) <= set(game["pings"]) and "map" in game:
            return game
    sys.path.append(str(REPO / "tools"))
    import replays
    replay = replays.load_replay(Path(path))
    info = replays.parse_map(replay.map)

    class Walker(replays._Walker):
        def __init__(self) -> None:
            super().__init__(info)
            self.pings = {k: array(c) for k, c in PING_COLUMNS.items()}
            self.states = {k: array(c) for k, c in STATE_COLUMNS.items()}

        def _start_turn(self, dragon_id: int) -> None:
            super()._start_turn(dragon_id)
            d = self.mover
            for k, v in zip(self.states, (self.round, d.id, d.team == "B", *d.body[0], len(d.body))):
                self.states[k].append(v)

        def _on_sonar(self, ping) -> None:
            super()._on_sonar(ping)
            sender = self.dragons[ping.senderId]
            head = sender.body[0] if sender.body else (-1, -1)
            hit = ping.hitId if ping.which() == "hitId" else -1
            row = (self.round, sender.team == "B", ping.value64 or ping.value, sender.id, hit, *head, len(sender.body))
            for k, v in zip(self.pings, row):
                self.pings[k].append(v)

    walker = Walker()
    for event in replay.events:
        walker.on_event(event)
    game = dict(meta=meta, W=info.width, H=info.height, map=info.name, pings=walker.pings, states=walker.states)
    cache.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_bytes(zlib.compress(pickle.dumps(game), 3))
    tmp.rename(out)
    return game


# The indices of `side`'s pings in `game`.
def sent_by(game: dict, side: int) -> list[int]:
    t = game["pings"]["t"]
    return [i for i in range(len(t)) if t[i] == side]


UNRANKED_OK = {952}  # teams whose unranked games count too (Cache me outside plays its real bot there)


# Each team's games to read, newest first: {team: [(meta, path, side)]}, ranked only (UNRANKED_OK: unranked too), from
# the ISO time `since` when given.
def team_games(rows: list[tuple[dict, str]], since: str | None = None) -> dict[int, list[tuple[dict, str, int]]]:
    per: dict[int, list[tuple[dict, str, int]]] = {}
    for meta, path in rows:
        if since and meta["at"] < since:
            continue
        for side, team in enumerate((meta["A"], meta["B"])):
            if team is not None and team != OURS and (meta["ranked"] or team in UNRANKED_OK):
                per.setdefault(team, []).append((meta, path, side))
    return per
