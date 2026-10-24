import json
import sys
sys.path.insert(0, '.')
from pathlib import Path
from collections import defaultdict, Counter

analysis_data = json.load(open('data/top50_deep_crypto_analysis.json'))

# Detailed protocol analyzer
teams = json.load(open('data/top50_teams.json'))

print("Analyzing specific team bitfield patterns...")

for team in teams:
    rank = team['rank']
    tid = str(team['id'])
    d = analysis_data.get(tid)
    if not d or d.get('status') == 'SILENT':
        continue
    
    samples = d.get('sample_pings', [])
    print(f"\n==========================================")
    print(f"Rank {rank}: {team['name']} (ID {tid})")
    print(f"Total sampled: {d.get('total_pings_sampled')}, Distinct: {d.get('distinct_vals')}")
    print(f"Multi-dir identical: {d.get('identical_multi_dir_pct')}%")
    print(f"Best Round: {d.get('best_round_shift')}")
    print(f"Best X: {d.get('best_x_shift')}")
    print(f"Best Y: {d.get('best_y_shift')}")
    print(f"Best Len: {d.get('best_len_shift')}")
    print(f"Best ID: {d.get('best_id_shift')}")
    
    for s in samples[:4]:
        v = int(s['val_hex'], 16)
        print(f"  r={s['round']:3d} id={s['id']:2d} pos=({s['x']:2d},{s['y']:2d}) L={s['len']:2d} dir={s['dir']:5s} hex={s['val_hex']}")
