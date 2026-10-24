"""Tag and relay finder.

Tags: the most common top 4, 8, 12 and 16 bits of a team's distinct values (a share far above 2^-bits is a tag),
and which bits are constant.
Relays: a value first sent by one dragon and later by another; the lag in rounds, and whether the relayer was hit by
it first (it heard it). A payload finder then tries each window of bits (the value without its low/high k bits) for
a part that is resent with changing edges: its lifetime in rounds, senders per payload, variants per payload (a
changing counter beside a fixed payload is a hop count or TTL).

    python3 tools/keyed/tags.py TEAM [--since ISO] [--games 6]

Reading (Team SKKU 249): top nibble 0xA on 100%; payload bits 4..43 lives <= 8 rounds, up to 39 senders, <= 3
variants in bits 0..3 and 44..59.
"""
import collections
import sys

import common as C


def main():
    args = sys.argv[1:]
    since = args[args.index('--since') + 1] if '--since' in args else None
    ng = int(args[args.index('--games') + 1]) if '--games' in args else 6
    D = {m: g for m, g in C.load(int(args[0]), since).items() if g['pings']}
    ms = sorted(D, key=lambda m: C.when(D[m]['meta']), reverse=True)[:ng]
    vals = {p['value'] for m in ms for p in D[m]['pings']}
    print('games', len(ms), 'distinct values', len(vals))
    if not vals:
        return
    for b in (4, 8, 12, 16):
        c = collections.Counter(v >> (64 - b) for v in vals)
        print(f'  top {b:2d} bits:', [(hex(k), round(n / len(vals), 3)) for k, n in c.most_common(3)], f'(chance {2 ** -b:.4f})')
    ones = [sum((v >> i) & 1 for v in vals) / len(vals) for i in range(64)]
    print('  constant bits:', [i for i in range(64) if ones[i] in (0.0, 1.0)])
    rel = collections.Counter(); lag = collections.Counter()
    for m in ms:
        first = {}; heard = collections.defaultdict(set)
        for p in D[m]['pings']:
            v, s = p['value'], p['sender']
            if v not in first:
                first[v] = (p['round'], s)
            elif first[v][1] != s:
                rel['relay heard' if v in heard[s] else 'relay not heard'] += 1
                lag[min(p['round'] - first[v][0], 10)] += 1
            if p['hit'] is not None and p['hitteam'] == D[m]['side']:
                heard[p['hit']].add(v)
    print('  resends by another dragon:', dict(rel), 'lag rounds (10 = 10+):', sorted(lag.items()))
    best = None
    for lo in (0, 4, 8, 12, 16):
        for hi in (0, 4, 8, 12, 16, 20):
            w = 64 - lo - hi
            if w < 24:
                continue
            life = collections.defaultdict(lambda: [10 ** 6, -1, set(), set()])
            for m in ms:
                for p in D[m]['pings']:
                    k = (m, (p['value'] >> lo) & ((1 << w) - 1))
                    e = life[k]
                    e[0] = min(e[0], p['round']); e[1] = max(e[1], p['round']); e[2].add(p['sender']); e[3].add(p['value'])
            multi = [e for e in life.values() if len(e[3]) > 1]
            score = len(multi) / len(life)
            if best is None or score > best[0]:
                best = (score, lo, w, life)
    score, lo, w, life = best
    lives = sorted(e[1] - e[0] for e in life.values())
    print(f'  payload candidate bits {lo}..{lo + w - 1}: {score:.3f} of payloads appear with 2+ edge variants; '
          f'life median {lives[len(lives) // 2]} max {lives[-1]} rounds; senders per payload max '
          f'{max(len(e[2]) for e in life.values())}; variants max {max(len(e[3]) for e in life.values())}')


if __name__ == '__main__':
    main()
