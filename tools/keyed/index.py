"""Builds DATA/games.jsonl: one row per match held in any common.SOURCES folder (fetched, data/replays, study,
ladder) plus the loop's own online games (~/.bc-loop/online-replays/*/index.json, team 157 on one side).

    python3 tools/keyed/index.py
"""
import glob
import json
import os

import common as C

rows = {}
for src, d in C.SOURCES:
    p = d / 'index.jsonl'
    if not p.exists():
        continue
    for l in open(p):
        r = json.loads(l)
        f = d / f"{r['match']}.replay"
        if r['match'] not in rows and f.exists():
            rows[r['match']] = dict(match=r['match'], src=src, path=str(f), ranked=r.get('ranked'), A=r['teamAId'],
                                    B=r['teamBId'], subA=r.get('submissionAId'), subB=r.get('submissionBId'),
                                    winner=r.get('winner'), map=r.get('map'), at=r.get('requestedAt') or r.get('fetchedAt'))
for ix in glob.glob(str(C.HOME / 'online-replays/*/index.json')):
    for g in json.load(open(ix))['games']:
        if g['id'] in rows or not g.get('replay') or not os.path.exists(g['replay']):
            continue
        ours = g['side'] == 'A'
        A, B = (157, g['opponent']) if ours else (g['opponent'], 157)
        sA, sB = (g['submission_id'], g.get('opponent_submission_id'))[:: 1 if ours else -1]
        w = ((g.get('summary') or {}).get('result') or {}).get('winner')
        rows[g['id']] = dict(match=g['id'], src='online', path=g['replay'], ranked=g.get('ranked'), A=A, B=B, subA=sA,
                             subB=sB, winner=w.lower() if w else None,
                             map=((g.get('summary') or {}).get('map') or {}).get('name'), at=g.get('requested_at'))
C.DATA.mkdir(parents=True, exist_ok=True)
with open(C.DATA / 'games.jsonl', 'w') as f:
    for r in sorted(rows.values(), key=lambda r: r['match']):
        f.write(json.dumps(r) + '\n')
print(len(rows), 'matches indexed into', C.DATA / 'games.jsonl')
