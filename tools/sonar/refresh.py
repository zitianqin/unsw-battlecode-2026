"""One command before a tournament: fetch the newest ranked replays, find the teams whose sonar changed, discover
their new formats, re-measure every entry and rewrite the bot's table. Prints which teams changed.

    .venv/bin/python tools/sonar/refresh.py --bot <folder with listen.hpp> --since 2026-10-10T08:00
        [--fetch [--teams 91 206 ...] [--battles 2]] [--dry-run]

1. --fetch: tools/replays.py fetch --team T --battles N --ranked for each team (--teams, else every team in the spec
   that sends anything), into data/replays (1 request a second: about 3 s a battle).
2. verify.py over the games since --since: the teams with a game group (a submission, or a day) of 200 values or more
   that no entry measured.json trusts (it detects or verifies) covers are "changed".
3. discover.py on each changed team's games since --since: new entries "replays-<day of --since>" (and "-tag"),
   appended to tools/sonar/spec.toml (with --dry-run, printed instead and nothing written).
4. verify.py over every game (--max-games a team), merged into tools/sonar/measured.json, then formats.py writes
   <bot>/listen_formats.hpp. Run the bot tests afterwards (their round-trip case covers the new rows).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.append(str(HERE))

import discover  # noqa: E402
import formats  # noqa: E402
import games as G  # noqa: E402
import verify  # noqa: E402


def fetch(teams: list[int], battles: int) -> None:
    for team in teams:
        print(f"fetching team {team}", flush=True)
        subprocess.run([sys.executable, str(G.REPO / "tools/replays.py"), "fetch", "--team", str(team), "--battles",
                        str(battles), "--ranked"], check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bot", type=Path, required=True, help="a bot folder with listen.hpp (its header is rewritten)")
    parser.add_argument("--since", required=True, help="ISO time (UTC): games from here are the new ones")
    parser.add_argument("--fetch", action="store_true", help="fetch each team's newest ranked battles first")
    parser.add_argument("--teams", type=int, nargs="*", help="teams to fetch (default: every team in the spec)")
    parser.add_argument("--battles", type=int, default=2, help="newest ranked battles to fetch per team")
    parser.add_argument("--max-games", type=int, default=1000, help="games per team for the final measurement")
    parser.add_argument("--cache", type=Path, default=G.CACHE)
    parser.add_argument("--dry-run", action="store_true", help="print the new entries; write nothing")
    args = parser.parse_args()
    specs = formats.load()
    if args.fetch:
        fetch(args.teams or sorted({f["team"] for f in specs if f["kind"] != "silent"}), args.battles)
    # Which entries to trust comes from the full measurement on file; which groups they cover, from the new games.
    known = json.loads(formats.MEASURED.read_text()) if formats.MEASURED.exists() else {}
    trusted = {k for k, m in known.items() if m.get("detects") or m.get("verified")}
    _, measured, t, _ = verify.run(args.bot, since=args.since, cache=args.cache)
    changed = verify.uncovered(specs, measured, t, trusted)
    print(f"{len(changed)} teams sent values since {args.since} that no detecting or verified entry covers:")
    names = {f["team"]: f["name"] for f in specs}
    new = []
    per_team = G.team_games(G.games(), args.since)
    for team, groups in changed.items():
        played = [(G.extract(meta, path, args.cache), side) for meta, path, side in per_team.get(team, [])[:15]]
        found = discover.entries(team, names.get(team, f"team {team}"), played, f"replays-{args.since[:10]}")
        have = {f["version"] for f in specs if f["team"] == team}
        found = [e for e in found if e["version"] not in have]
        new += found
        kinds = ", ".join(f"{e['version']} ({e['kind']}, {len(e.get('layout', []))} layouts)" for e in found)
        print(f"- {team} {names.get(team, '')}: {', '.join(groups)} -> {kinds or 'already has this version'}")
    if args.dry_run:
        print("\n".join(formats.entry_toml(e) for e in new))
        return 0
    if new:
        formats.append(new)
        print(f"appended {len(new)} entries to {formats.SPEC}")
    specs, measured, t, _ = verify.run(args.bot, max_games=args.max_games, cache=args.cache, update=True)
    formats.write(args.bot)
    for e in new:
        m = measured[f"{e['team']}/{e['version']}"]
        print(f"  {e['team']}/{e['version']}: cover {m['cover']:.2f}, head {m['head']:.2f}, stray {m['stray']:.2g}, "
              f"{'detects' if m['detects'] else 'does not detect'}, {'verified' if m['verified'] else 'unverified'}")
    print(f"wrote {args.bot / 'listen_formats.hpp'}; stream check: {dict(t['locks'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
