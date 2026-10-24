import json
import sys
sys.path.insert(0, '.')
from pathlib import Path
import time
import tools.replays as tr

top300 = json.load(open('data/top300_teams.json'))
out_dir = Path('data/replays')
index_file = out_dir / 'index.jsonl'

def get_covered_team_ids():
    if not index_file.exists():
        return set()
    lines = [json.loads(l) for l in index_file.read_text().splitlines() if l.strip()]
    ranked = [l for l in lines if l.get('ranked')]
    covered = set()
    for l in ranked:
        m = l['match']
        if (out_dir / f"{m}.replay").exists():
            if l.get('teamAId'): covered.add(l['teamAId'])
            if l.get('teamBId'): covered.add(l['teamBId'])
    return covered

covered = get_covered_team_ids()
print(f"Initially covered top 300 teams: {len(set(t['id'] for t in top300) & covered)} / 300")

http = tr.Http()
fetcher = tr.Fetcher(http, out_dir, 1000, tr.read_index(index_file))

needed_teams = [t for t in top300 if t['id'] not in covered]
print(f"Teams needing fetch: {len(needed_teams)}")

for i, team in enumerate(needed_teams):
    tid = team['id']
    tname = team['name']
    trank = team['rank']
    
    # Check if newly covered by an opponent battle
    covered = get_covered_team_ids()
    if tid in covered:
        print(f"[{i+1}/{len(needed_teams)}] Rank {trank}: {tname} (ID {tid}) already covered by opponent replay!")
        continue
        
    print(f"[{i+1}/{len(needed_teams)}] Fetching ranked battle for Rank {trank}: {tname} (ID {tid})...")
    try:
        team_page = tr.page_data(http, f"/teams/{tid}")
        recent = team_page.get("recent") or []
        ranked_recent = [b for b in recent if b.get("ranked")]
        
        if not ranked_recent:
            print(f"  No ranked battles found in recent battles for {tname}")
            continue
            
        mids = fetcher.battle_ids(ranked_recent, 1)
        for mid in mids:
            fetcher.battle(mid)
            
    except Exception as e:
        print(f"  Error fetching team {tid} ({tname}): {e}")
        
    time.sleep(0.4)

covered = get_covered_team_ids()
print(f"\nFetch completed! Final top 300 coverage: {len(set(t['id'] for t in top300) & covered)} / 300 teams.")
