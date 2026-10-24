"""Recurrence tables: does a team reuse values across rounds and games, and on what schedule does it send?

- values seen in more than one round (0: the round is an input);
- per round, distinct values over senders (1.0: every dragon its own value; low: a shared codebook);
- held-out recurrence: a table round -> values seen in the *other* games; the share of each game's sending turns
  whose value is in it (a recognition rate a listener would get from our replays), also by map;
- schedule: best (id + round) % P == c for P in 2..8, and the send rate on and off it.

    python3 tools/keyed/recur.py TEAM [--since ISO] [--save]

--save writes DATA/recur/<team>.json: {round: [values seen in 2+ games]}, the lookup table a listener could copy.
Reading (801, 28 Sep 20:00 - 1 Oct): held-out 0.279 (n 517,266); sends on 92.0% of turns when (id+round)%4==0.
"""
import collections
import json
import sys

import common as C


def main(team, since, save):
    games = {m: g for m, g in C.load(team, since).items() if g['pings']}
    turns = {m: C.turns(g) for m, g in games.items()}
    n = sum(len(t) for t in turns.values())
    print('games', len(games), 'sending turns', n)
    rounds_of = collections.defaultdict(set)
    ratio = collections.Counter()
    table = collections.defaultdict(collections.Counter)  # round -> value -> games
    for m, t in turns.items():
        per = collections.defaultdict(dict)
        for p in t:
            rounds_of[p['value']].add(p['round'])
            per[p['round']][p['sender']] = p['value']
        for r, sv in per.items():
            if len(sv) >= 8:
                ratio[round(len(set(sv.values())) / len(sv), 1)] += 1
            for v in set(sv.values()):
                table[r][v] += 1
    print('values in more than one round', sum(1 for s in rounds_of.values() if len(s) > 1), 'of', len(rounds_of))
    print('distinct values / senders, rounds with 8+ senders:', sorted(ratio.items()))
    hit = 0
    bymap = collections.defaultdict(lambda: [0, 0])
    for m, t in turns.items():
        mine = collections.Counter((p['round'], p['value']) for p in t)
        own = {k: 1 for k in mine}
        for p in t:
            inside = table[p['round']][p['value']] - own[(p['round'], p['value'])] > 0
            hit += inside
            bymap[games[m]['map']][0] += inside
            bymap[games[m]['map']][1] += 1
    print(f'held-out recurrence: {hit / max(n, 1):.3f} of sending turns carry a value another game sent that round')
    print('   by map:', {k: f'{a / b:.3f} (n {b})' for k, (a, b) in sorted(bymap.items())})
    for P in range(2, 9):
        c = collections.Counter((p['sender'] + p['round']) % P for t in turns.values() for p in t)
        k, top = c.most_common(1)[0]
        print(f'   schedule P {P}: (id+round)%{P}=={k} holds for {top / max(n, 1):.3f} of sends (1/{P} = {1 / P:.3f})')
    for P, k in [(2, 0), (4, 0)]:
        send = [0, 0]; tot = [0, 0]
        for m, g in games.items():
            sent = {(p['round'], p['sender']) for p in turns[m]}
            for rd, hs in g['heads'].items():
                for i in hs:
                    on = (i + rd) % P == k
                    tot[on] += 1
                    send[on] += (rd, i) in sent
        print(f'   send rate when (id+round)%{P}=={k}: {send[1] / max(tot[1], 1):.3f} (n {tot[1]}), '
              f'otherwise {send[0] / max(tot[0], 1):.3f} (n {tot[0]})')
    if save:
        (C.DATA / 'recur').mkdir(parents=True, exist_ok=True)
        out = {r: sorted(v for v, k in c.items() if k > 1) for r, c in sorted(table.items())}
        json.dump(out, open(C.DATA / 'recur' / f'{team}.json', 'w'))
        print('saved', C.DATA / 'recur' / f'{team}.json', sum(len(v) for v in out.values()), 'values')


if __name__ == '__main__':
    since = sys.argv[sys.argv.index('--since') + 1] if '--since' in sys.argv else None
    main(int(sys.argv[1]), since, '--save' in sys.argv)
