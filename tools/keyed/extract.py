"""Pulls every sonar ping one team sends in its ranked games (DATA/games.jsonl) with the state around it, into
DATA/pings/<team>.pkl. One process; run it under `nice -n 20`, it walks every event of every replay.

    python3 tools/keyed/extract.py TEAM [--since ISO]

Pickle: {match: dict(meta, W, H, map, seed, side, sub, pings=[...], heads={round: {id: (head, length)}})}; each ping
has round, sender, dir (the direction the ray left in), value (64-bit), head/length/facing after the move and
head0/len0/face0 before it, tail, born, parent, nth (the sender's n-th ping this game), order (turn order in the
round), kind (what the ray hit), hit, hitteam.
"""
import argparse
import pickle
import sys
from pathlib import Path

import common as C
import replays as R


class W(R._Walker):
    def __init__(s, info, side):
        super().__init__(info)
        s.side = side
        s.pings = []
        s.heads = {}
        s.head0 = s.len0 = s.face0 = None
        s.count = {}
        s.order = 0

    def on_event(s, ev):
        k = ev.which()
        if k == 'roundStart':
            s.order = 0
        if k == 'turnStart':
            d = s.dragons[ev.turnStart.id]
            s.head0, s.len0, s.face0 = d.body[0], len(d.body), d.facing
            s.order += 1
        super().on_event(ev)
        if k == 'roundStart':
            s.heads[s.round] = {i: (d.body[0], len(d.body)) for i, d in s.dragons.items() if d.alive and d.team == s.side}
        elif k == 'sonarPing':
            p = ev.sonarPing
            snd = s.dragons[p.senderId]
            if snd.team != s.side:
                return
            hit = p.hitId if p.which() == 'hitId' else None
            n = s.count.get(p.senderId, 0)
            s.count[p.senderId] = n + 1
            s.pings.append(dict(round=s.round, sender=p.senderId, dir=str(p.direction), value=p.value64,
                                head=snd.body[0], length=len(snd.body), facing=snd.facing, tail=snd.body[-1],
                                head0=s.head0, len0=s.len0, face0=s.face0, born=snd.born, parent=snd.parent,
                                nth=n, order=s.order, kind=str(p.hitKind), hit=hit,
                                hitteam=s.dragons[hit].team if hit in s.dragons else None))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('team', type=int)
    ap.add_argument('--since')
    a = ap.parse_args()
    t0 = C.iso(a.since) if a.since else 0
    out = {}
    for g in C.games_index():
        if a.team not in (g['A'], g['B']) or g['A'] == g['B'] or not g['ranked'] or C.when(g) < t0:
            continue
        side = 'A' if g['A'] == a.team else 'B'
        try:
            r = R.load_replay(Path(g['path']))
        except Exception as e:  # a truncated download: say so and go on
            print('unreadable', g['match'], e, file=sys.stderr)
            continue
        info = R.parse_map(r.map)
        w = W(info, side)
        for ev in r.events:
            w.on_event(ev)
        out[g['match']] = dict(meta=g, W=info.width, H=info.height, map=info.name,
                               seed=r.seed.value if r.seed.which() == 'value' else None, side=side,
                               sub=g['sub' + side], pings=w.pings, heads=w.heads)
    (C.DATA / 'pings').mkdir(parents=True, exist_ok=True)
    pickle.dump(out, open(C.DATA / 'pings' / f'{a.team}.pkl', 'wb'))
    print(a.team, 'ranked games', len(out), 'pings', sum(len(v['pings']) for v in out.values()))


if __name__ == '__main__':
    main()
