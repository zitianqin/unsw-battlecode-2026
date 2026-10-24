"""Inverts common 64-bit bijective mixers on a team's values and looks for structure in the result.

A value that is mixer(plaintext) looks random, but mixer^-1(value) then has fixed or rarely-set bits (a small
plaintext packed in low bits). For each candidate inverse we print the most biased bits and the share of results
below 2^40 (a packed position/round/length fits there). A random value gives bias ~0.5 +- 0.002 at our n, and 2^-24.

    python3 tools/keyed/mixers.py TEAM [N] [--since ISO]
"""
import sys, pickle, random
from pathlib import Path

HERE = Path(__file__).resolve().parent
M = (1 << 64) - 1


def inv_mul(a):
    return pow(a, -1, 1 << 64)


def unxorshr(z, s):
    x = z
    for _ in range(64 // s + 1):
        x = z ^ (x >> s)
    return x & M


def unxorshl(z, s):
    x = z
    for _ in range(64 // s + 1):
        x = (z ^ (x << s)) & M
    return x


def inv_splitmix_fin(z):  # z ^= z>>30; z*=bf58..; z ^= z>>27; z*=94d0..; z ^= z>>31
    z = unxorshr(z, 31)
    z = (z * inv_mul(0x94d049bb133111eb)) & M
    z = unxorshr(z, 27)
    z = (z * inv_mul(0xbf58476d1ce4e5b9)) & M
    return unxorshr(z, 30)


def inv_fmix64(k):  # murmur3: k^=k>>33; k*=ff51..; k^=k>>33; k*=c4ce..; k^=k>>33
    k = unxorshr(k, 33)
    k = (k * inv_mul(0xc4ceb9fe1a85ec53)) & M
    k = unxorshr(k, 33)
    k = (k * inv_mul(0xff51afd7ed558ccd)) & M
    return unxorshr(k, 33)


def inv_xorshift64(x, a=13, b=7, c=17):  # x^=x<<a; x^=x>>b; x^=x<<c
    x = unxorshl(x, c)
    x = unxorshr(x, b)
    return unxorshl(x, a)


def inv_xorshift64star(v):  # output = x * 2545F..., state stepped with 12,25,27 (x^=x>>12; x^=x<<25; x^=x>>27)
    x = (v * inv_mul(0x2545F4914F6CDD1D)) & M
    x = unxorshr(x, 27)
    x = unxorshl(x, 25)
    return unxorshr(x, 12)


def inv_moremur(z):  # z ^= z>>27; z*=3C79AC492BA7B653; z^=z>>33; z*=1C69B3F74AC4AE35; z^=z>>27
    z = unxorshr(z, 27)
    z = (z * inv_mul(0x1C69B3F74AC4AE35)) & M
    z = unxorshr(z, 33)
    z = (z * inv_mul(0x3C79AC492BA7B653)) & M
    return unxorshr(z, 27)


def inv_rrmxmx(z):  # Pelle Evensen: v ^= ror(v,49)^ror(v,24); v*=9FB21C651E98DF25; v^=v>>28; v*=same; v^=v>>28
    z = unxorshr(z, 28)
    z = (z * inv_mul(0x9FB21C651E98DF25)) & M
    z = unxorshr(z, 28)
    z = (z * inv_mul(0x9FB21C651E98DF25)) & M
    return z  # (skipping the rotate step: rarely matters for the bias test)


GOLD = 0x9e3779b97f4a7c15
CANDS = {
    'identity': lambda v: v,
    'splitmix_fin': inv_splitmix_fin,
    'splitmix_fin-gold': lambda v: (inv_splitmix_fin(v) - GOLD) & M,
    'fmix64': inv_fmix64,
    'xorshift64': inv_xorshift64,
    'xorshift64*': inv_xorshift64star,
    'moremur': inv_moremur,
    'rrmxmx-part': inv_rrmxmx,
    'mul_gold': lambda v: (v * inv_mul(GOLD)) & M,
    'mul_2545': lambda v: (v * inv_mul(0x2545F4914F6CDD1D)) & M,
    'mul_ff51': lambda v: (v * inv_mul(0xff51afd7ed558ccd)) & M,
    'lcg_knuth': lambda v: ((v - 1442695040888963407) * inv_mul(6364136223846793005)) & M,
}


def profile(vals):
    n = len(vals)
    out = []
    for name, f in CANDS.items():
        xs = [f(v) for v in vals]
        bias = [sum((x >> b) & 1 for x in xs) / n for b in range(64)]
        worst = sorted(range(64), key=lambda b: -abs(bias[b] - 0.5))[:4]
        small = sum(1 for x in xs if x < (1 << 40)) / n
        out.append((max(abs(bias[b] - 0.5) for b in range(64)), name, small, [(b, round(bias[b], 3)) for b in worst]))
    return sorted(out, reverse=True)


if __name__ == '__main__':
    import common as C
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    since = sys.argv[sys.argv.index('--since') + 1] if '--since' in sys.argv else None
    if since in args:
        args.remove(since)
    team = int(args[0]); N = int(args[1]) if len(args) > 1 else 20000
    D = C.load(team, since)
    vals = list({p['value'] for g in D.values() for p in g['pings']})
    random.seed(1)
    vals = random.sample(vals, min(N, len(vals)))
    print('distinct values sampled', len(vals))
    for row in profile(vals):
        print(f'{row[1]:20s} max bias {row[0]:.3f}  <2^40 {row[2]:.4f}  {row[3]}')
