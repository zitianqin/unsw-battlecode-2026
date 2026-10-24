"""Field finder: where a team packs its state into the value, if it does.

For each state field (round, sender id, head x/y after and before the move, length after/before, tile = y*W + x) and
each bit offset, the share of origin sends (the first send of each value in a game, so relays don't blur it) for
which ((value >> offset) & mask) XOR field (or minus field) equals one constant. Fitted on the newest --fit games,
then each field that fits >= 0.9 is checked on the next 6 older games (held out). A field holding >= 0.95 on
held-out games is decoded: the printed line is the decoder.

--round-key also tries a constant that changes every round (a per-round key), against a shuffled baseline (the field
taken from another send of the same round): a share well above the baseline means a round-keyed field.

    python3 tools/keyed/fieldfind.py TEAM [--since ISO] [--fit 3] [--round-key]

Reading (545, 29 Sep): round = ((v>>47)&0x1FF)^0x83 fits 0.999, held out 1.000 (n 3,637); tile at bits 22..33
xor 0xE95, id at 34..43 xor 0x230, length at 12..18 xor 0x1E fit 0.56-0.66 (the self-report message type only).
"""
import collections
import random
import sys

import common as C

WIDTH = {'round': 9, 'id': 10, 'x': 6, 'y': 6, 'x0': 6, 'y0': 6, 'len': 7, 'len0': 7, 'tile': 12, 'tile0': 12}


def origin_rows(g):
    seen = set(); out = []
    for p in g['pings']:
        if p['value'] in seen:
            continue
        seen.add(p['value'])
        (x, y), (x0, y0) = p['head'], p['head0']
        out.append((p['value'], dict(round=p['round'], id=p['sender'], x=x, y=y, x0=x0, y0=y0, len=p['length'],
                                     len0=p['len0'], tile=y * g['W'] + x, tile0=y0 * g['W'] + x0)))
    return out


def fits(v, st, f, s, w, op, k):
    mask = (1 << w) - 1
    fv = (v >> s) & mask
    return (fv ^ (st[f] & mask) if op == 'xor' else (fv - st[f]) & mask) == k


def scan(rows):
    res = {}
    for f, w in WIDTH.items():
        mask = (1 << w) - 1
        for s in range(0, 65 - w):
            cx = collections.Counter(); cs = collections.Counter()
            for v, st in rows:
                fv = (v >> s) & mask
                cx[fv ^ (st[f] & mask)] += 1
                cs[(fv - st[f]) & mask] += 1
            (kx, nx), (ks, ns) = cx.most_common(1)[0], cs.most_common(1)[0]
            best = max((nx / len(rows), s, w, 'xor', kx), (ns / len(rows), s, w, 'sub', ks))
            if f not in res or best[0] > res[f][0]:
                res[f] = best
    return res


def round_keyed(games_rows):
    random.seed(9)
    out = {}
    for f, w in WIDTH.items():
        if f == 'round':
            continue
        mask = (1 << w) - 1
        for s in range(0, 65 - w):
            hit = base = tot = 0
            for rows in games_rows:
                byr = collections.defaultdict(list)
                for v, st in rows:
                    byr[st['round']].append(((v >> s) & mask, st[f] & mask))
                for lst in byr.values():
                    if len(lst) < 3:
                        continue
                    hit += collections.Counter(a ^ b for a, b in lst).most_common(1)[0][1]
                    sh = [b for _, b in lst]; random.shuffle(sh)
                    base += collections.Counter(a ^ b for (a, _), b in zip(lst, sh)).most_common(1)[0][1]
                    tot += len(lst)
            if tot and (f not in out or hit / tot > out[f][0]):
                out[f] = (hit / tot, base / tot, s, tot)
    return out


def main():
    args = sys.argv[1:]
    since = args[args.index('--since') + 1] if '--since' in args else None
    fit = int(args[args.index('--fit') + 1]) if '--fit' in args else 3
    team = int(args[0])
    D = {m: g for m, g in C.load(team, since).items() if g['pings']}
    ms = sorted(D, key=lambda m: C.when(D[m]['meta']), reverse=True)
    rows = [r for m in ms[:fit] for r in origin_rows(D[m])]
    if not rows:
        print('no sends'); return
    print('fit games', ms[:fit], 'origin sends', len(rows))
    res = scan(rows)
    for f, (share, s, w, op, k) in sorted(res.items(), key=lambda kv: -kv[1][0]):
        print(f'  {f:6s} fit {share:.3f}: (({"v"} >> {s}) & {hex((1 << w) - 1)}) {"^" if op == "xor" else "-"} {f} == {k:#x}')
    good = {f: r for f, r in res.items() if r[0] >= 0.9}
    for m in ms[fit:fit + 6]:
        hr = origin_rows(D[m])
        line = [f'{f} {sum(fits(v, st, f, *r[1:]) for v, st in hr) / max(len(hr), 1):.3f}' for f, r in good.items()]
        print('  held-out', m, D[m]['meta'].get('at', '')[:16], 'n', len(hr), ', '.join(line) or '(no field fits)')
    if '--round-key' in args:
        for f, (share, base, s, tot) in round_keyed([origin_rows(D[m]) for m in ms[:fit]]).items():
            print(f'  round-keyed {f:6s} {share:.3f} (shuffled {base:.3f}) at bit {s}, n {tot}')


if __name__ == '__main__':
    main()
