import json
import sys
sys.path.insert(0, '.')
from pathlib import Path
from collections import defaultdict, Counter
import time
import tools.replays as tr

t0 = time.time()

# Load top 300 teams
top300 = json.load(open('data/top300_teams.json'))
tids_map = {t['id']: t for t in top300}

# Load index
index_lines = [json.loads(l) for l in Path('data/replays/index.jsonl').read_text().splitlines() if l.strip()]
match_meta_map = {l['match']: l for l in index_lines}

# Find all relevant matches where at least one team is in top 300
ranked_matches = set()
for l in index_lines:
    ta = l.get('teamAId')
    tb = l.get('teamBId')
    if ta in tids_map or tb in tids_map:
        m = l['match']
        if (Path('data/replays') / f"{m}.replay").exists():
            ranked_matches.add(m)

print(f"Total relevant match replays to process for Top 300: {len(ranked_matches)}")

# Ping collectors
team_pings = defaultdict(list)
team_total_counts = Counter()
team_hits = defaultdict(Counter)
team_dirs = defaultdict(Counter)
team_matches_used = defaultdict(set)

processed = 0
for m in sorted(ranked_matches):
    replay_path = Path(f'data/replays/{m}.replay')
    if not replay_path.exists():
        continue
    meta = match_meta_map.get(m, {})
    tid_a = meta.get('teamAId')
    tid_b = meta.get('teamBId')
    
    match_pings = []
    
    def make_on_sonar(cur_pings):
        def custom_on_sonar(self, ping):
            sender = self.dragons[ping.senderId]
            kind = str(ping.hitKind)
            if kind == "unknown":
                if ping.which() == "hitId" and ping.hitId in self.dragons:
                    target = self.dragons[ping.hitId]
                    head = target.body and target.body[0] == (ping.end.x, ping.end.y)
                    kind = ("ally" if target.team == sender.team else "enemy") + ("Head" if head else "")
                else:
                    kind = "empty"
            cur_pings.append((
                self.round,
                sender.id,
                sender.team,
                sender.body[0][0],
                sender.body[0][1],
                len(sender.body),
                str(ping.direction),
                ping.value64 or ping.value,
                kind
            ))
        return custom_on_sonar

    tr._Walker._on_sonar = make_on_sonar(match_pings)
    try:
        rep = tr.load_replay(replay_path)
        game = tr.analyse(rep)
        width, height = game.map.width, game.map.height
    except Exception as e:
        continue
        
    for rd, sid, steam, hx, hy, pl, pdir, val, hit in match_pings:
        tid = tid_a if steam == 'A' else tid_b
        if tid and tid in tids_map:
            team_total_counts[tid] += 1
            team_hits[tid][hit] += 1
            team_dirs[tid][pdir] += 1
            team_matches_used[tid].add(m)
            if len(team_pings[tid]) < 8000:
                team_pings[tid].append((rd, sid, hx, hy, pl, pdir, val, hit, m, width, height))
                
    processed += 1
    if processed % 50 == 0:
        print(f"Processed {processed}/{len(ranked_matches)} matches ({time.time()-t0:.1f}s)...")

print(f"Extraction complete in {time.time()-t0:.1f}s. Now running deep cryptanalysis across all 300 teams...")

top300_analysis = {}

for team in top300:
    rank = team['rank']
    tid = team['id']
    name = team['name']
    elo = team.get('elo', 0)
    pings = team_pings.get(tid, [])
    total_p = team_total_counts[tid]
    matches_list = sorted(list(team_matches_used.get(tid, [])))
    
    if total_p == 0:
        top300_analysis[tid] = {
            'rank': rank, 'name': name, 'id': tid, 'elo': elo,
            'status': 'SILENT', 'family': 'Silent (No Sonar)',
            'total_pings': 0, 'distinct_values': 0,
            'ranked_sample_matches': matches_list,
            'hits': {}, 'directions': {}, 'sample_pings': []
        }
        continue
        
    vals = [p[6] for p in pings]
    distinct_vals = len(set(vals))
    
    # Masks
    bitwise_or = 0
    bitwise_and = 0xFFFFFFFFFFFFFFFF if vals else 0
    for v in vals:
        bitwise_or |= v
        bitwise_and &= v
        
    bit_lengths = Counter(v.bit_length() for v in vals)
    top_p12 = Counter(hex((v >> 52) & 0xFFF) for v in vals).most_common(3)
    top_p16 = Counter(hex((v >> 48) & 0xFFFF) for v in vals).most_common(3)
    
    # 1. SSS Standard check
    sss_exact_coords = 0
    sss_exact_round = 0
    sss_magics = Counter()
    for rd, sid, hx, hy, pl, pdir, v, hit, m, w, h in pings:
        mag = (v >> 52) & 0xFFF
        sub = (v >> 38) & 0x3FFF
        l = (v >> 31) & 0x7F
        x = (v >> 25) & 0x3F
        y = (v >> 19) & 0x3F
        r = (v >> 10) & 0x1FF
        if x == hx and y == hy:
            sss_exact_coords += 1
            sss_magics[hex(mag)] += 1
        if r == rd:
            sss_exact_round += 1
            
    is_sss = (sss_exact_coords > 0.25 * len(pings)) or (sss_exact_round > 0.35 * len(pings))
    
    # 2. General Bitfield Shift Search
    x_shifts = Counter()
    y_shifts = Counter()
    round_shifts = Counter()
    len_shifts = Counter()
    id_shifts = Counter()
    tile_shifts = Counter()
    
    sample_pings = pings[:1500]
    sample_n = len(sample_pings)
    for rd, sid, hx, hy, pl, pdir, v, hit, m, w, h in sample_pings:
        tile_idx = hy * w + hx
        for s in range(59):
            if ((v >> s) & 0x3F) == hx: x_shifts[s] += 1
            if ((v >> s) & 0x3F) == hy: y_shifts[s] += 1
            if ((v >> s) & 0x1FF) == rd: round_shifts[s] += 1
            if ((v >> s) & 0x3F) == pl: len_shifts[s] += 1
            if ((v >> s) & 0x1F) == sid: id_shifts[s] += 1
            if s <= 52 and ((v >> s) & 0xFFF) == tile_idx: tile_shifts[s] += 1
            
    best_x = x_shifts.most_common(1)
    best_y = y_shifts.most_common(1)
    best_rd = round_shifts.most_common(1)
    best_len = len_shifts.most_common(1)
    best_id = id_shifts.most_common(1)
    best_tile = tile_shifts.most_common(1)
    
    # 3. Omnidirectional symmetry
    rounds_by_dragon = defaultdict(list)
    for rd, sid, hx, hy, pl, pdir, v, hit, m, w, h in pings:
        rounds_by_dragon[(m, rd, sid)].append((pdir, v))
        
    multi_dir_count = 0
    identical_multi_dir = 0
    for (m, rd, sid), plist in rounds_by_dragon.items():
        if len(plist) > 1:
            multi_dir_count += 1
            if len(set(x[1] for x in plist)) == 1:
                identical_multi_dir += 1
                
    multi_pct = round(identical_multi_dir * 100 / multi_dir_count, 2) if multi_dir_count else 0.0
    
    # 4. Token enum
    is_token_enum = (distinct_vals <= 60)
    top_tokens = [(hex(tok), cnt) for tok, cnt in Counter(vals).most_common(6)]
    
    # Categorize family
    if is_sss:
        mag = top_p12[0][0] if top_p12 else '0xF5E'
        fam = f"SSS Telemetry Family (Magic {mag})"
    elif rank == 1:
        fam = "Directional + Static Beacons"
    elif best_x and best_y and best_x[0][1]/sample_n > 0.18 and best_y[0][1]/sample_n > 0.18:
        fam = "Custom Plaintext Bitfield"
    elif best_tile and best_tile[0][1]/sample_n > 0.2:
        fam = "Packed Tile Index Telemetry"
    elif is_token_enum:
        fam = "Discrete State Token Dictionary"
    elif multi_pct >= 85.0:
        fam = "Keyed PRNG / Rolling Hash"
    elif team_hits[tid]['ally'] + team_hits[tid]['allyHead'] > 0.5 * total_p:
        fam = "Targeted Mesh Gossip"
    else:
        fam = "Structured Bitfield / Clustered"

    top300_analysis[tid] = {
        'rank': rank,
        'name': name,
        'id': tid,
        'elo': elo,
        'status': 'ACTIVE',
        'family': fam,
        'total_pings': total_p,
        'distinct_values': distinct_vals,
        'ranked_sample_matches': matches_list[:5],
        'bitwise_or': hex(bitwise_or),
        'bitwise_and': hex(bitwise_and),
        'bit_lengths': {str(k): v for k, v in bit_lengths.most_common(3)},
        'top_prefixes_12': {k: v for k, v in top_p12},
        'top_prefixes_16': {k: v for k, v in top_p16},
        'hits': dict(team_hits[tid]),
        'directions': dict(team_dirs[tid]),
        'is_sss': is_sss,
        'sss_exact_coords_pct': round(sss_exact_coords * 100 / len(pings), 2) if pings else 0,
        'sss_magics': dict(sss_magics),
        'best_x_shift': (best_x[0][0], round(best_x[0][1]*100/sample_n, 1)) if best_x else None,
        'best_y_shift': (best_y[0][0], round(best_y[0][1]*100/sample_n, 1)) if best_y else None,
        'best_round_shift': (best_rd[0][0], round(best_rd[0][1]*100/sample_n, 1)) if best_rd else None,
        'best_len_shift': (best_len[0][0], round(best_len[0][1]*100/sample_n, 1)) if best_len else None,
        'best_id_shift': (best_id[0][0], round(best_id[0][1]*100/sample_n, 1)) if best_id else None,
        'best_tile_shift': (best_tile[0][0], round(best_tile[0][1]*100/sample_n, 1)) if best_tile else None,
        'identical_multi_dir_pct': multi_pct,
        'is_token_enum': is_token_enum,
        'top_tokens': top_tokens,
        'sample_pings': [{
            'round': p[0], 'id': p[1], 'x': p[2], 'y': p[3],
            'len': p[4], 'dir': p[5], 'val_hex': hex(p[6]),
            'val_dec': str(p[6]), 'hit': p[7]
        } for p in pings[:8]]
    }

Path('data/top300_analysis_results.json').write_text(json.dumps(top300_analysis, indent=2))
print(f"Top 300 cryptanalysis successfully finished in {time.time()-t0:.1f}s and saved to data/top300_analysis_results.json!")
