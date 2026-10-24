import sys, json, pickle
sys.path.insert(0, '/Users/me/Dev/unsw-battlecode-2026/tools')
import replays as R
from pathlib import Path

D = Path('/Users/me/.bc-loop/study-replays')
idx = [json.loads(l) for l in open(D / 'index.jsonl')]
WANT = {91, 470, 46}

class W(R._Walker):
    def __init__(s, info):
        super().__init__(info)
        s.pearls = set()
        s.cd = {}  # tile -> (reset value, round set)
        s.pings = []; s.snaps = {}; s.head0=None; s.len0=0; s.face0=None
        for line in []: pass
    def on_event(s, ev):
        k = ev.which()
        if k == 'tileChange':
            t = (ev.tileChange.tile.x, ev.tileChange.tile.y)
            (s.pearls.add if ev.tileChange.hasPearl else s.pearls.discard)(t)
        elif k == 'pearlCountdown':
            t = (ev.pearlCountdown.tile.x, ev.pearlCountdown.tile.y)
            s.cd[t] = (ev.pearlCountdown.countdown, s.round)
        elif k == 'turnStart':
            d = s.dragons[ev.turnStart.id]; s.head0 = d.body[0]; s.len0 = len(d.body); s.face0 = d.facing
        elif k == 'roundStart':
            s.snaps[s.round] = {i: (d.team, d.body[0], len(d.body)) for i, d in s.dragons.items() if d.alive}
        elif k == 'sonarPing':
            p = ev.sonarPing
            snd = s.dragons[p.senderId]
            hit = p.hitId if p.which() == 'hitId' else None
            ht = s.dragons[hit].team if hit is not None and hit in s.dragons else None
            s.pings.append(dict(round=s.round, sender=p.senderId, team=snd.team, dir=str(p.direction),
                facing=snd.facing, value=p.value64 or p.value, v32=p.value, origin=(p.origin.x, p.origin.y),
                end=(p.end.x, p.end.y), kind=str(p.hitKind), hit=hit, hitteam=ht,
                head=snd.body[0], tail=snd.body[-1], length=len(snd.body), nd=sum(1 for d in s.dragons.values() if d.alive and d.team==snd.team),
                pearls=frozenset(s.pearls), head0=s.head0, len0=s.len0, face0=s.face0))
        super().on_event(ev)

out = {}
for g in idx:
    if not ({g['teamAId'], g['teamBId']} & WANT):
        continue
    f = D / f"{g['match']}.replay"
    if not f.exists(): continue
    r = R.load_replay(f)
    info = R.parse_map(r.map)
    w = W(info)
    for ev in r.events:
        w.on_event(ev)
    # initial dragons
    out[g['match']] = dict(meta=g, W=info.width, H=info.height, mapname=info.name, pings=w.pings,
                           dragons={i: (d.team, d.born, d.parent) for i, d in w.dragons.items()}, snaps=w.snaps)
    print(g['match'], g['teamAName'], g['teamBName'], info.name, len(w.pings), file=sys.stderr)
pickle.dump(out, open('pings.pkl', 'wb'))
