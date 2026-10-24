import json
import sys
sys.path.insert(0, '.')
from pathlib import Path
from collections import defaultdict, Counter
import time
import tools.replays as tr

t0 = time.time()

# Load team definitions and index
teams = json.load(open('data/top50_teams.json'))
ranked_res = json.load(open('data/top50_ranked_results.json'))
team_matches_map = {r['id']: r['ranked_sample_matches'] for r in ranked_res}
index_lines = [json.loads(l) for l in Path('data/replays/index.jsonl').read_text().splitlines() if l.strip()]
match_meta_map = {l['match']: l for l in index_lines}

# Find all unique matches needed across top 50
all_matches = set()
for r in ranked_res:
    for m in r['ranked_sample_matches']:
        all_matches.add(m)

print(f"Total unique matches to process: {len(all_matches)}")

# team_id -> list of tuples: (round, sender_id, head_x, head_y, length, dir, val, hit, match_id)
team_pings = defaultdict(list)
team_total_counts = Counter()

# Process all matches in a single pass
processed_matches = 0
for m in sorted(all_matches):
    replay_path = Path(f'data/replays/{m}.replay')
    if not replay_path.exists():
        continue
    meta = match_meta_map.get(m, {})
    tid_a = meta.get('teamAId')
    tid_b = meta.get('teamBId')
    
    # Ping collector for this match
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
            # Don't call original to save CPU/memory stats tracking
        return custom_on_sonar

    tr._Walker._on_sonar = make_on_sonar(match_pings)
    rep = tr.load_replay(replay_path)
    game = tr.analyse(rep)
    
    for rd, sid, steam, hx, hy, pl, pdir, val, hit in match_pings:
        tid = tid_a if steam == 'A' else tid_b
        if tid:
            team_total_counts[tid] += 1
            # Keep up to 15,000 pings per team for deep cryptanalysis (plenty for exact reverse-engineering)
            if len(team_pings[tid]) < 15000:
                team_pings[tid].append((rd, sid, hx, hy, pl, pdir, val, hit, m))
                
    processed_matches += 1
    if processed_matches % 15 == 0:
        print(f"Processed {processed_matches}/{len(all_matches)} replays ({time.time()-t0:.1f}s)...")

print(f"Replay extraction complete in {time.time()-t0:.1f}s. Now running cryptanalysis on all 50 teams...")

analysis_results = {}

for team in teams:
    rank = team['rank']
    tid = team['id']
    name = team['name']
    pings = team_pings.get(tid, [])
    total_p = team_total_counts[tid]
    
    if total_p == 0:
        analysis_results[tid] = {
            'rank': rank, 'name': name, 'id': tid, 'status': 'SILENT', 'total_pings': 0
        }
        continue
        
    distinct_vals = len({p[6] for p in pings})
    
    # 1. SSS Standard check
    sss_exact_coords = 0
    sss_exact_round = 0
    sss_magics = Counter()
    for rd, sid, hx, hy, pl, pdir, v, hit, m in pings:
        mag = (v >> 52) & 0xFFF
        sub = (v >> 38) & 0x3FFF
        l = (v >> 31) & 0x7F
        x = (v >> 25) & 0x3F
        y = (v >> 19) & 0x3F
        r = (v >> 10) & 0x1FF
        chk = v & 0x3FF
        if x == hx and y == hy:
            sss_exact_coords += 1
            sss_magics[hex(mag)] += 1
        if r == rd:
            sss_exact_round += 1
            
    is_sss = (sss_exact_coords > 0.4 * len(pings)) or (sss_exact_round > 0.4 * len(pings))
    
    # 2. General Bitfield Search for X, Y, Round, Length, ID
    x_shifts = Counter()
    y_shifts = Counter()
    round_shifts = Counter()
    len_shifts = Counter()
    id_shifts = Counter()
    
    sample_pings = pings[:2000]
    sample_n = len(sample_pings)
    for rd, sid, hx, hy, pl, pdir, v, hit, m in sample_pings:
        for s in range(59):
            if ((v >> s) & 0x3F) == hx:
                x_shifts[s] += 1
            if ((v >> s) & 0x3F) == hy:
                y_shifts[s] += 1
            if ((v >> s) & 0x1FF) == rd:
                round_shifts[s] += 1
            if ((v >> s) & 0x3F) == pl:
                len_shifts[s] += 1
            if ((v >> s) & 0x1F) == sid:
                id_shifts[s] += 1
                
    best_x = x_shifts.most_common(1)
    best_y = y_shifts.most_common(1)
    best_rd = round_shifts.most_common(1)
    best_len = len_shifts.most_common(1)
    best_id = id_shifts.most_common(1)
    
    # 3. Directional / Omnidirectional correlation
    rounds_by_dragon = defaultdict(list)
    for rd, sid, hx, hy, pl, pdir, v, hit, m in pings:
        rounds_by_dragon[(m, rd, sid)].append((pdir, v))
        
    multi_dir_count = 0
    identical_multi_dir = 0
    dir_diffs = Counter()
    for (m, rd, sid), plist in rounds_by_dragon.items():
        if len(plist) > 1:
            multi_dir_count += 1
            vals = [x[1] for x in plist]
            if len(set(vals)) == 1:
                identical_multi_dir += 1
            else:
                for i in range(len(plist)):
                    for j in range(i+1, len(plist)):
                        d1, v1 = plist[i]
                        d2, v2 = plist[j]
                        diff = v1 ^ v2
                        dir_diffs[f"{d1}^{d2}:{hex(diff)[:8]}"] += 1
                        
    # 4. Token Enum Check
    val_counts = Counter(p[6] for p in pings)
    is_token_enum = (distinct_vals <= 50)
    top_tokens = [(hex(v), c) for v, c in val_counts.most_common(10)]
    
    # 5. Temporal correlation / delta / xor
    dragon_series = defaultdict(list)
    for rd, sid, hx, hy, pl, pdir, v, hit, m in pings:
        dragon_series[(m, sid)].append((rd, v))
        
    deltas = Counter()
    xors = Counter()
    for (m, sid), seq in dragon_series.items():
        seq.sort()
        for i in range(len(seq)-1):
            r1, v1 = seq[i]
            r2, v2 = seq[i+1]
            if r2 == r1 + 1:
                deltas[(v2 - v1) & 0xFFFFFFFFFFFFFFFF] += 1
                xors[v1 ^ v2] += 1
                
    top_delta = deltas.most_common(3)
    top_xor = xors.most_common(3)
    
    # 6. SSS Checksum test if SSS
    chk_rule = None
    if is_sss:
        # Check if checksum matches any simple formula
        # Let's test:
        # 1. CRC10 with common polys
        # 2. Sum of bits or fields % 1024
        # 3. XOR of bytes % 1024
        chk_matches = Counter()
        for rd, sid, hx, hy, pl, pdir, v, hit, m in pings[:500]:
            target_chk = v & 0x3FF
            body = v >> 10
            # Test simple modular sums
            if (body % 1024) == target_chk:
                chk_matches['body_mod_1024'] += 1
            if ((body * 31) % 1024) == target_chk:
                chk_matches['body_31_mod_1024'] += 1
            if (((v >> 10) ^ (v >> 20) ^ (v >> 30) ^ (v >> 40) ^ (v >> 50)) & 0x3FF) == target_chk:
                chk_matches['xor_10bit_slices'] += 1
        if chk_matches:
            chk_rule = chk_matches.most_common(1)[0]

    res = {
        'rank': rank,
        'name': name,
        'id': tid,
        'total_pings_sampled': len(pings),
        'total_pings_in_matches': total_p,
        'distinct_vals': distinct_vals,
        'is_sss': is_sss,
        'sss_exact_coords_pct': round(sss_exact_coords * 100 / len(pings), 2) if pings else 0,
        'sss_magics': dict(sss_magics),
        'chk_rule': chk_rule,
        'best_x_shift': (best_x[0][0], round(best_x[0][1]*100/sample_n, 1)) if best_x else None,
        'best_y_shift': (best_y[0][0], round(best_y[0][1]*100/sample_n, 1)) if best_y else None,
        'best_round_shift': (best_rd[0][0], round(best_rd[0][1]*100/sample_n, 1)) if best_rd else None,
        'best_len_shift': (best_len[0][0], round(best_len[0][1]*100/sample_n, 1)) if best_len else None,
        'best_id_shift': (best_id[0][0], round(best_id[0][1]*100/sample_n, 1)) if best_id else None,
        'multi_dir_count': multi_dir_count,
        'identical_multi_dir_pct': round(identical_multi_dir * 100 / multi_dir_count, 2) if multi_dir_count else 0,
        'dir_diffs': dict(dir_diffs.most_common(5)),
        'is_token_enum': is_token_enum,
        'top_tokens': top_tokens,
        'top_delta': [(hex(d), c) for d, c in top_delta],
        'top_xor': [(hex(x), c) for x, c in top_xor],
        'sample_pings': [{
            'round': p[0], 'id': p[1], 'x': p[2], 'y': p[3],
            'len': p[4], 'dir': p[5], 'val_hex': hex(p[6])
        } for p in pings[:8]]
    }
    analysis_results[tid] = res

Path('data/top50_deep_crypto_analysis.json').write_text(json.dumps(analysis_results, indent=2))
print(f"Deep cryptanalysis complete for all 50 teams in {time.time()-t0:.1f}s!")
