"""Cross-game state match: which state fields decide a team's value. For each field set F, among sending turns (the
first ray of each turn) that agree on F, the share of pairs with the same value. A share near 1 means F decides the
value (a hash or code of F); ~0 with random 64-bit values means F does not. Pairs within one game (field 'm') are
the only ones for sets that include it.

    python3 tools/keyed/statematch.py TEAM [--since ISO]

Reading (cheji bt 70, 29 Sep - 1 Oct): side+id+round+body+W+H 1.000 (n 1,391 pairs), without side 0.43, without
round 0.05: value = hash of own state. 801: the round-only row is what recur.py measures.
"""
import collections
import sys

import common as C

SETS = [
    ('side', 'id', 'round', 'body', 'W', 'H'),
    ('side', 'id', 'round', 'body'),
    ('id', 'round', 'body', 'W', 'H'),
    ('side', 'round', 'body', 'W', 'H'),
    ('side', 'id', 'body', 'W', 'H'),
    ('side', 'id', 'round', 'head', 'length', 'W', 'H'),
    ('side', 'id', 'round', 'head', 'W', 'H'),
    ('map', 'side', 'id', 'round', 'head0', 'head', 'len0', 'length'),
    ('map', 'side', 'id', 'round'),
    ('round', 'head', 'length'),
    ('round',),
    ('m', 'round'),
]


def rows(games):
    out = []
    for m, g in games.items():
        for p in C.turns(g):
            out.append(dict(m=m, map=g['map'], side=g['side'], W=g['W'], H=g['H'], round=p['round'], id=p['sender'],
                            head=p['head'], head0=p['head0'], length=p['length'], len0=p['len0'],
                            body=(p['head'], p['tail'], p['length']), v=p['value']))
    return out


def table(rs):
    for F in SETS:
        groups = collections.defaultdict(list)
        for r in rs:
            groups[tuple(r[f] for f in F)].append(r['v'])
        pairs = same = 0
        for vs in groups.values():
            if len(vs) > 1:
                pairs += len(vs) * (len(vs) - 1) // 2
                same += sum(k * (k - 1) // 2 for k in collections.Counter(vs).values())
        print(f'{"+".join(F):42s} pairs {pairs:11d} same value {same / max(pairs, 1):.4f}')


if __name__ == '__main__':
    since = sys.argv[sys.argv.index('--since') + 1] if '--since' in sys.argv else None
    rs = rows(C.load(int(sys.argv[1]), since))
    print('sending turns', len(rs), '(body = head, tail and length after the move)')
    table(rs)
