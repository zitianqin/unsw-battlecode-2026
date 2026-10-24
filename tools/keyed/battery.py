"""The standard test battery for one team's sonar, per version, on ranked games only. Writes DATA/battery/<team>.json
(common.DATA) and prints one JSON line per version; show.py prints it compactly, changed.py compares versions.

Versions: games grouped by submission id while the site named them, then decoys.versions' 20-game blocks; blocks
with the same traffic signature are merged afterwards by hand in the report. The decoy check (loop/decoys.py) is
applied to the team's ranked games and its verdict recorded; a flagged latest version is analysed on its own, not
dropped (a tournament may use it).

Tests per version (each is a way to tell content from noise):
- traffic: rays per sending turn, distinct values per turn, share of a dragon's turns with a send, by round band;
  ray direction relative to facing; what rays hit.
- determinism: across games, turns with the same (map, side, sender, round, head before/after, length before/after)
  and whether their values match; different states' XOR popcount (32 = avalanche).
- reuse: values repeated in another round (0 means round is an input), same-round values shared by two dragons.
- bits: per-bit bias against the binomial noise band; the top 12-bit prefixes; GF(2) linearity of each value bit in
  the state bits after removing a per-(game, round) key (pairs within one game-round).
- mixers: the best bias after inverting common unkeyed 64-bit mixers (mixers.py).
- chains: whether the next value is a function of the previous one, LCG solve.
- reaction: share of receivers that turn on their next turn after a teammate's ray hit them, against turns with no
  hit (content would change behaviour; confounded by position, so a hint only).

    python3 tools/keyed/battery.py TEAM [TEAM ...] [--since ISO]
"""
import sys, json, collections, itertools, random

import common as C
import decoys
import mixers as X

DIRS = ['north', 'east', 'south', 'west']
BANDS = [(0, 49), (50, 149), (150, 299), (300, 499)]


def when(g):
    return C.when(g['meta'])


def score(g):
    w = g['meta'].get('winner')
    if w in ('a', 'b'):
        return 1.0 if w.upper() == g['side'] else 0.0
    return 0.5


def state_bits(p, W, H):
    x, y = p['head']
    x0, y0 = p['head0']
    fields = [(x, 6), (y, 6), (x0, 6), (y0, 6), (p['length'], 7), (p['len0'], 7), (p['sender'], 10), (p['round'], 9),
              (DIRS.index(p['facing']) if p['facing'] in DIRS else 0, 2), (W, 6), (H, 6)]
    out = 0; pos = 0
    for v, n in fields:
        out |= (v & ((1 << n) - 1)) << pos
        pos += n
    return out, pos


def gf2_linear_bits(samples, nvars):
    """samples: [(state int, value)] differences. For each value bit, is it a GF(2)-linear function of the state
    bits? Solve on the first 3/4 by elimination, test on the last 1/4; returns the bits that predict >= 99%."""
    random.shuffle(samples)
    cut = len(samples) * 3 // 4
    fit, test = samples[:cut], samples[cut:]
    good = []
    for b in range(64):
        rows = [(s, (v >> b) & 1) for s, v in fit]
        basis = {}  # pivot -> (row, rhs)
        consistent = True
        for s, r in rows:
            while s:
                piv = s.bit_length() - 1
                if piv in basis:
                    bs, br = basis[piv]
                    s ^= bs; r ^= br
                else:
                    basis[piv] = (s, r)
                    break
            else:
                if r:
                    consistent = False
                    break
        if not consistent:
            continue
        # back-substitute to a solution vector
        sol = 0
        for piv in sorted(basis):
            bs, br = basis[piv]
            rest = bs & ~(1 << piv)
            val = br ^ (bin(rest & sol).count('1') & 1)
            if val:
                sol |= 1 << piv
        ok = sum(1 for s, v in test if (bin(s & sol).count('1') & 1) == ((v >> b) & 1))
        if test and ok / len(test) >= 0.99:
            good.append(b)
    return good


def analyse(games):
    st = collections.Counter()
    rays = collections.Counter(); distinct = collections.Counter(); rel = collections.Counter(); kinds = collections.Counter()
    band_send = collections.Counter(); band_turns = collections.Counter()
    same_state = collections.defaultdict(dict)
    rounds_of = collections.defaultdict(set)
    bit = [0] * 64; nvals = 0
    prefix = collections.Counter()
    shared_turns = 0; turns_total = 0
    gf_samples = []
    chain = collections.defaultdict(collections.Counter)
    react = {'ally': [0, 0], 'none': [0, 0]}
    vals_all = set()
    per_round_pings = collections.defaultdict(list)
    for m, g in games.items():
        W, H = g['W'], g['H']
        turns = collections.defaultdict(list)
        for p in g['pings']:
            turns[(p['round'], p['sender'])].append(p)
        # own dragons' turns (alive at round start) for the sending rate
        for rd, hs in g['heads'].items():
            for b0, b1 in BANDS:
                if b0 <= rd <= b1:
                    band_turns[(b0, b1)] += len(hs)
        byround = collections.defaultdict(dict)
        last = {}
        facing_at = {}
        for (rd, s), ps in turns.items():
            p0 = ps[0]
            vs = [p['value'] for p in ps]
            rays[len(ps)] += 1
            distinct[len(set(vs))] += 1
            for b0, b1 in BANDS:
                if b0 <= rd <= b1:
                    band_send[(b0, b1)] += 1
            for p in ps:
                kinds[p['kind']] += 1
                if p['facing'] in DIRS and p['dir'] in DIRS:
                    rel[('forward', 'right', 'back', 'left')[(DIRS.index(p['dir']) - DIRS.index(p['facing'])) % 4]] += 1
            v = p0['value']
            vals_all.add(v)
            rounds_of[v].add(rd)
            byround[rd].setdefault(v, set()).add(s)
            key = (g['map'], g['side'], s, rd, p0['head0'], p0['head'], p0['len0'], p0['length'])
            same_state[key].setdefault(m, v)
            per_round_pings[(m, rd)].append((state_bits(p0, W, H)[0], v))
            if s in last and last[s][0] == rd - 1:
                chain[last[s][1]][v] += 1
            last[s] = (rd, v)
            facing_at[(rd, s)] = p0['facing']
        for rd, d in byround.items():
            for v, ss in d.items():
                turns_total += len(ss)
                if len(ss) > 1:
                    shared_turns += len(ss)
        # reaction: a dragon hit by a ray in round r (own team's or enemy's value) turns on its next send?
        hitby = {}
        for p in g['pings']:
            if p['hit'] is not None and p['hitteam'] == g['side']:
                hitby[(p['round'], p['hit'])] = 'ally'
        for (rd, s), f in facing_at.items():
            nxt = facing_at.get((rd + 1, s))
            prev = facing_at.get((rd - 1, s))
            if nxt is None or prev is None:
                continue
            k = hitby.get((rd, s), 'none')
            react[k][0] += 1
            react[k][1] += nxt != f
    for v in random.sample(sorted(vals_all), min(20000, len(vals_all))):
        nvals += 1
        for b in range(64):
            bit[b] += (v >> b) & 1
        prefix[v >> 52] += 1
    # determinism
    ss_same = ss_diff = 0
    for key, gm in same_state.items():
        vs = list(gm.values())
        for a, b in itertools.combinations(vs, 2):
            if a == b:
                ss_same += 1
            else:
                ss_diff += 1
    # avalanche between different states in the same (map, side, sender, round)
    grp = collections.defaultdict(list)
    for key, gm in same_state.items():
        for m, v in gm.items():
            grp[key[:4]].append((key[4:], v))
    pops = []
    for k, lst in grp.items():
        for (s1, v1), (s2, v2) in itertools.combinations(lst[:6], 2):
            if s1 != s2 and v1 != v2:
                pops.append(bin(v1 ^ v2).count('1'))
    # GF(2): pairs within one (game, round): value xor vs state xor
    diffs = []
    for k, lst in per_round_pings.items():
        for (s1, v1), (s2, v2) in zip(lst, lst[1:]):
            if v1 != v2:
                diffs.append((s1 ^ s2, v1 ^ v2))
    random.shuffle(diffs)
    diffs = diffs[:4000]
    lin = gf2_linear_bits(diffs, 77) if len(diffs) > 400 else None
    # direct GF(2): value bits linear in state bits with no key
    direct = []
    for k, lst in per_round_pings.items():
        direct += lst[:2]
    random.shuffle(direct)
    lin_direct = gf2_linear_bits(direct[:4000], 77) if len(direct) > 400 else None
    maxbias = max(abs(b / nvals - 0.5) for b in bit) if nvals else None
    noise = 4 * (0.25 / max(nvals, 1)) ** 0.5
    mixer_best = None
    if nvals:
        sample = random.sample(sorted(vals_all), min(5000, len(vals_all)))
        prof = X.profile(sample)
        mixer_best = (prof[0][1], round(prof[0][0], 4), round(prof[0][2], 4))
    multi = [c for c in chain.values() if sum(c.values()) > 1]
    return dict(
        sending_turns=sum(rays.values()), pings=sum(k * v for k, v in rays.items()),
        rays_per_turn=dict(sorted(rays.items())), distinct_per_turn=dict(sorted(distinct.items())),
        send_rate_by_round={f'{a}-{b}': round(band_send[(a, b)] / band_turns[(a, b)], 3) if band_turns[(a, b)] else None
                            for a, b in BANDS},
        direction_vs_facing=dict(rel), hits=dict(kinds),
        same_state_pairs=ss_same + ss_diff, same_state_same_value=round(ss_same / max(ss_same + ss_diff, 1), 4),
        diff_state_pairs=len(pops), diff_state_mean_popcount=round(sum(pops) / len(pops), 2) if pops else None,
        values=len(vals_all), values_in_two_rounds=sum(1 for s in rounds_of.values() if len(s) > 1),
        shared_same_round_share=round(shared_turns / max(turns_total, 1), 4),
        max_bit_bias=round(maxbias, 4) if maxbias is not None else None, bias_noise_4sd=round(noise, 4),
        biased_bits=[b for b in range(64) if nvals and abs(bit[b] / nvals - 0.5) > noise],
        constant_bits=[b for b in range(64) if nvals and bit[b] in (0, nvals)],
        top12=[(hex(p), c) for p, c in prefix.most_common(3)], top12_share=round(prefix.most_common(1)[0][1] / nvals, 4) if nvals else None,
        gf2_bits_linear_after_round_key=lin, gf2_bits_linear_direct=lin_direct,
        mixer_best=mixer_best,
        chain_prev_seen_twice=len(multi), chain_single_successor=sum(1 for c in multi if len(c) == 1),
        reaction={k: (v[0], round(v[1] / v[0], 3) if v[0] else None) for k, v in react.items()},
    )


def main(team, since=None):
    allg = C.load(team, since)
    ranked = allg
    rows = sorted(((g['sub'], when(g), score(g), m) for m, g in ranked.items()), key=lambda r: r[1])
    dec = decoys.check([(s, t, sc) for s, t, sc, m in rows])
    # map each game to its version label the same way versions() does
    named = max((i for i, r in enumerate(rows) if r[0] is not None), default=-1)
    label_of = {}
    for s, t, sc, m in rows[:named + 1]:
        if s is not None:
            label_of[m] = f'submission {s}'
    unnamed = [r for r in rows[named + 1:]]
    for end in range(len(unnamed), 0, -decoys.BLOCK):
        chunk = unnamed[max(0, end - decoys.BLOCK):end]
        lab = f'unnamed {decoys.stamp(chunk[0][1])} to {decoys.stamp(chunk[-1][1])}'
        for r in chunk:
            label_of[r[3]] = lab
    dropped = [m for s, t, sc, m in rows if m not in label_of]  # unnamed games before the last named one
    out = dict(team=team, since=since, games_ranked=len(ranked), ranked_unnamed_before_last_named_dropped=len(dropped), decoy=dec and {k: dec[k] for k in ('latest', 'p', 'flagged', 'won', 'n', 'best', 'best_won', 'best_n')},
               versions=[])
    order = []
    for m in sorted(label_of, key=lambda m: when(ranked[m])):
        if label_of[m] not in order:
            order.append(label_of[m])
    random.seed(7)
    for lab in order:
        gs = {m: ranked[m] for m in label_of if label_of[m] == lab}
        res = analyse(gs)
        first = min(when(g) for g in gs.values()); last = max(when(g) for g in gs.values())
        res.update(label=lab, games=len(gs), matches=sorted(gs), first=decoys.stamp(first), last=decoys.stamp(last),
                   won=sum(score(g) for g in gs.values()))
        out['versions'].append(res)
        print(json.dumps({k: res[k] for k in res if k != 'matches'}, ensure_ascii=False))
    (C.DATA / 'battery').mkdir(parents=True, exist_ok=True)
    json.dump(out, open(C.DATA / 'battery' / f'{team}.json', 'w'), ensure_ascii=False, indent=1)
    print(json.dumps({k: out[k] for k in out if k != 'versions'}, ensure_ascii=False))


if __name__ == '__main__':
    since = sys.argv[sys.argv.index('--since') + 1] if '--since' in sys.argv else None
    for t in sys.argv[1:]:
        if t.isdigit():
            main(int(t), since)
