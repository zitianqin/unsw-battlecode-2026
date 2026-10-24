import json
import sys
sys.path.insert(0, '.')
from pathlib import Path
from collections import defaultdict, Counter

data = json.load(open('data/top50_deep_crypto_analysis.json'))

remaining_ranks = [1, 2, 4, 8, 9, 13, 15, 16, 21, 23, 24, 25, 27, 29, 30, 31, 34, 35, 36, 38, 40, 41, 43, 45, 50]

print(f"Deep analyzing {len(remaining_ranks)} remaining teams...")

for r in remaining_ranks:
    # find team entry
    entry = None
    for tid, d in data.items():
        if d.get('rank') == r:
            entry = d
            break
    if not entry:
        continue
    
    samples = entry.get('sample_pings', [])
    top_tok = entry.get('top_tokens', [])
    top_delta = entry.get('top_delta', [])
    top_xor = entry.get('top_xor', [])
    print(f"\n--- Rank {r}: {entry['name']} (ID {entry['id']}) ---")
    print(f"Distinct: {entry['distinct_vals']}, Identical Multi-dir: {entry.get('identical_multi_dir_pct')}%")
    print(f"Top 3 tokens: {top_tok[:3]}")
    print(f"Top 3 deltas: {top_delta[:3]}")
    print(f"Top 3 xors: {top_xor[:3]}")
    for s in samples[:3]:
        print(f"  r={s['round']} id={s['id']} pos=({s['x']},{s['y']}) L={s['len']} dir={s['dir']} hex={s['val_hex']}")
