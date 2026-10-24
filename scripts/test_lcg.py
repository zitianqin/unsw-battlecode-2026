import json
import sys
sys.path.insert(0, '.')
from pathlib import Path
from collections import defaultdict

data = json.load(open('data/top50_deep_crypto_analysis.json'))

# Extended GCD to find modular inverse
def egcd(a, b):
    if a == 0:
        return (b, 0, 1)
    else:
        g, y, x = egcd(b % a, a)
        return (g, x - (b // a) * y, y)

def modinv(a, m):
    g, x, y = egcd(a, m)
    if g != 1:
        return None
    else:
        return x % m

# Test for LCG: X[n+1] = A * X[n] + C mod 2^64
# (X[2] - X[1]) = A * (X[1] - X[0]) mod 2^64
def test_lcg(seq):
    if len(seq) < 4:
        return None
    # seq is list of ints
    M = 1 << 64
    for i in range(len(seq) - 3):
        x0, x1, x2, x3 = seq[i], seq[i+1], seq[i+2], seq[i+3]
        d0 = (x1 - x0) % M
        d1 = (x2 - x1) % M
        d2 = (x3 - x2) % M
        if d0 % 2 == 1:
            inv = modinv(d0, M)
            if inv:
                A = (d1 * inv) % M
                C = (x1 - A * x0) % M
                # Verify on next step
                if (A * x2 + C) % M == x3:
                    return (hex(A), hex(C), 64)
    # Test 32-bit LCG
    M32 = 1 << 32
    for i in range(len(seq) - 3):
        x0, x1, x2, x3 = seq[i] & 0xFFFFFFFF, seq[i+1] & 0xFFFFFFFF, seq[i+2] & 0xFFFFFFFF, seq[i+3] & 0xFFFFFFFF
        d0 = (x1 - x0) % M32
        d1 = (x2 - x1) % M32
        d2 = (x3 - x2) % M32
        if d0 % 2 == 1:
            inv = modinv(d0, M32)
            if inv:
                A = (d1 * inv) % M32
                C = (x1 - A * x0) % M32
                if (A * x2 + C) % M32 == x3:
                    return (hex(A), hex(C), 32)
    return None

prng_ranks = [2, 4, 8, 9, 13, 15, 16, 21, 23, 24, 30, 31, 34, 50]
print("Testing LCG on PRNG teams...")

for r in prng_ranks:
    entry = None
    for tid, d in data.items():
        if d.get('rank') == r:
            entry = d
            break
    if not entry:
        continue
    samples = entry.get('sample_pings', [])
    vals = [int(s['val_hex'], 16) for s in samples]
    lcg_res = test_lcg(vals)
    print(f"Rank {r:2d} ({entry['name'][:20]}): LCG={lcg_res}")
