"""Did a team's sonar format change? Reads DATA/battery/<team>.json (battery.py) and gives each version a format
signature; prints each version's signature and FORMAT CHANGED where it differs from the version before.

Signature: silent; rays per sending turn (mode); values per turn (mode); same value on all rays; top-12-bit tag
(when one prefix covers >= 20% of values); constant bits (the same in every sampled value); values reused in another round (>1%);
same-round sharing between dragons (>20%); determinism (same-state pairs >= 95% equal, when there are 20+ pairs).
The send rate is shown but not compared (it moves with maps and opponents).

    python3 tools/keyed/changed.py TEAM [--baseline FILE]

--baseline compares the newest version against the newest version of another battery file (for example one saved
before the rerun: cp DATA/battery/70.json /tmp/70-before.json), so a change since the last analysis shows even
when the new data has only one version.
"""
import json
import sys

import common as C


def signature(v):
    if not v['sending_turns']:
        return ('silent',)
    mode = lambda d: max(d.items(), key=lambda kv: kv[1])[0]
    tag = v['top12'][0][0] if v['top12_share'] and v['top12_share'] >= 0.2 else None
    fixed = tuple(v.get('constant_bits', []))
    det = (v['same_state_same_value'] >= 0.95) if v['same_state_pairs'] >= 20 else None
    return (f"rays {mode(v['rays_per_turn'])}", f"values/turn {mode(v['distinct_per_turn'])}", f'tag {tag}',
            f'constant bits {list(fixed)}', f"reused across rounds {v['values_in_two_rounds'] / max(v['values'], 1) > 0.01}",
            f"shared in round {v['shared_same_round_share'] > 0.2}", f'deterministic {det}')


def same(a, b):
    # a determinism verdict of None (too few pairs) matches anything
    return len(a) == len(b) and all(x == y or (x.startswith('deterministic') and 'None' in (x[-4:], y[-4:]))
                                    for x, y in zip(a, b))


def main():
    team = int(sys.argv[1])
    b = json.load(open(C.DATA / 'battery' / f'{team}.json'))
    prev = None
    for v in b['versions']:
        sig = signature(v)
        flag = '' if prev is None or same(prev, sig) else '  FORMAT CHANGED'
        print(f"{v['label']:40s} games {v['games']:3d} rate {v['send_rate_by_round']} {sig}{flag}")
        prev = sig
    if '--baseline' in sys.argv:
        base = json.load(open(sys.argv[sys.argv.index('--baseline') + 1]))
        a, c = signature(base['versions'][-1]), signature(b['versions'][-1])
        print('baseline newest:', base['versions'][-1]['label'], a)
        print('now newest:     ', b['versions'][-1]['label'], c)
        print('FORMAT CHANGED since the baseline' if not same(a, c) else 'same format as the baseline')


if __name__ == '__main__':
    main()
