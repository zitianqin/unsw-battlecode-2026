"""Downloads a team's ranked games played since a time into DATA/replays (common.DATA), reproducibly: the battle list
comes from the site's /battles?teams=<team> pages (tools/replays.py battles_between), the replays through
tools/replays.py's Fetcher, which skips games already cached and appends DATA/replays/index.jsonl.

    python3 tools/keyed/fetch.py TEAM --since 2026-10-01T00:00Z [--until ISO] [--max-games N]
"""
import argparse
import sys
import time

import common as C
import replays as R


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('team', type=int)
    ap.add_argument('--since', required=True)
    ap.add_argument('--until')
    ap.add_argument('--max-games', type=int)
    ap.add_argument('--max-pages', type=int, default=30)
    a = ap.parse_args()
    out = C.DATA / 'replays'
    out.mkdir(parents=True, exist_ok=True)
    http = R.Http()
    until = C.iso(a.until) if a.until else time.time()
    rows, complete = R.battles_between(lambda n: R.page_data(http, f'/battles?teams={a.team}&page={n}'),
                                       C.iso(a.since), until, a.max_pages)
    rows = [b for b in rows if b.get('ranked')]
    fetcher = R.Fetcher(http, out, a.max_games, R.read_index(out / 'index.jsonl'))
    for mid in fetcher.battle_ids(rows):
        if not fetcher.budget_left():
            break
        fetcher.battle(mid)
    print(f'team {a.team}: {len(rows)} ranked battles since {a.since} (list complete: {complete}); '
          f'downloaded {fetcher.downloaded} replays, {fetcher.skipped} battles skipped', file=sys.stderr)


if __name__ == '__main__':
    main()
