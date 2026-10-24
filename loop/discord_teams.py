"""Which ladder team each Discord poster is on, so the Discord digest can weigh a claim by the poster's rank and point
at that team's replays (loop/DISCORD.md), and every team's current name and rank for whoever labels a team, keyed by
its id: a name or rank copied into a record goes stale while the id stays right (Vibing++ became Cutlery on
2026-09-28 while the briefing still said Vibing++ and rank 2). Each refresh keeps a team's last PREVIOUS names, so a
post or record under an old name maps to its id, and alerts each rename and top-rank move that matters (`news`).
Names the loop saved elsewhere (`seen_names`: replay indexes, study history) join a team's older names at every read,
so a rename a reader missed (a process on older code writing teams.json without them, or before the first read that
kept them) is still recorded once some saved game or study used the old name.

The site signs people in through Discord, so a team member's `avatarUrl` on the site is usually
`https://cdn.discordapp.com/avatars/<Discord account id>/<hash>.png`, the same account id discord_read.py keeps for
each message. Members without one (Discord's default avatar, or a gravatar or site avatar for a non-Discord sign-in)
are indexed by their site username instead, a weaker match: anyone can choose a site username.

The upkeep calls `refresh` hourly, before the Discord read (maintenance.tick). One /leaderboard call gives every
team's name, rank, elo, wins (its row has no losses or draws) and dev flag, and since 2026-09-26 its members too; a
team whose row lists no members has its /teams/<id> page fetched instead, one a second, only when it was last tried
(fetched or failed) over a day ago, ranked teams first, at most FETCHES a run, saving every SAVE_EVERY fetches so a
stopped run resumes where it left off. It never raises: a failure is recorded in `error` (the leaderboard read
failing, or `pages_error`: over half the team pages of the last run that tried any failing), with `failing_since` the
first failed refresh since the last good one, and the last good lists stay. A teams.json of the wrong shape
(including a time that is not finite or not between 1970 and 2100) is read as empty, rebuilt, and said so in `error`
of the next save. <loop home>/discord/teams.json holds

    attempt_at, fetched_at (the last leaderboard read), error, pages_error, failing_since,
    failed (team pages that failed this run),
    teams:     {team id: {name, rank, elo, wins, dev, has_bot, members: [{username, discord_id}], members_at,
                          tried_at, members_from, first_seen, name_since (absent when unknown),
                          previous: [older names, latest first]}}
    accounts:  {Discord account id: {team_id, team, rank, elo, wins, dev}}
    usernames: {site username, lowercased: the same, for members without a Discord account id}

    .venv/bin/python loop/discord_teams.py   # refresh now and print the counts
"""

from __future__ import annotations

import json
import math
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path

from home import loop_home
from meta import alert, locked

EVERY = 3600  # the upkeep refreshes ranks this often
MEMBERS_EVERY = 24 * 3600  # a team page is fetched again (after a success or a failure) after this long
FETCHES = 300  # team pages fetched in one run at most (about 5 minutes), so a run never holds the upkeep long
SAVE_EVERY = 20
PACE = 1.0  # seconds between team page requests, as ladder.pull_replays paces its downloads
OUR_TEAM = "157"  # our team; its name and members are in CLAUDE.md (Decisions)
DISCORD_AVATAR = re.compile(r"^https://cdn\.discordapp\.com/avatars/(\d+)/")
# What json.dumps(ensure_ascii=False) leaves raw but a reader may take as a line break or a control: DEL, the C1
# controls (U+0085 is a line break) and the line and paragraph separators. C0 controls it already escapes.
UNSAFE = re.compile("[\x7f-\x9f  ]")
PREVIOUS = 5  # a team's older names kept, latest first
# Rank alerts: one line a read when rank 1 changes hands or a team enters or leaves ranks 1 to ALERT_RANKS (the leader
# study's set, maintenance.STUDY_KEEP), none for swaps inside them. On study-history.jsonl's 42 reads over 55.8 h to
# 2026-09-28 08:51 UTC that is 18 lines (7.7 a day; all teams crossing the top 5's edge, 1 with a new rank 1), where
# a line per changed rank within the top 5 gave 79 (34 a day). Each needs a decision, so that is the budget to watch.
# Renames alert for ranks 1 to RENAME_RANKS, dev teams and the teams we played this version.
ALERT_RANKS = 5
RENAME_RANKS = 10


def path_in(state: Path) -> Path:
    return state / "discord" / "teams.json"


# A name anyone can pick (a team's, a Discord display name) as one quoted, escaped token: it can't end the quote,
# start a new line or carry a control character.
def quoted_name(text: str) -> str:
    return UNSAFE.sub(lambda c: f"\\u{ord(c[0]):04x}", json.dumps(text, ensure_ascii=False))


LARGEST = 10 ** 15  # no elo, rank or id comes near this; json.loads reads 1e999 and 10**400 without complaint
LATEST = 4102444800  # 2100-01-01 UTC: a time field past it (or before 1970) is damage, not a time


# A finite int or float (not a bool) of sane size: json.loads also yields NaN, Infinity and ints too big for a float.
def number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return abs(value) < LARGEST if isinstance(value, int) else math.isfinite(value) and abs(value) < LARGEST


# A time teams.json keeps: seconds since 1970, before 2100, so datetime.fromtimestamp takes it.
def moment(value: object) -> bool:
    return number(value) and 0 <= value <= LATEST


def whole(value: object) -> bool:
    return isinstance(value, int) and number(value)


def rank_ok(value: object) -> bool:
    return value is None or (whole(value) and value >= 0)


def member_ok(m: object) -> bool:
    return (isinstance(m, dict) and isinstance(m.get("username"), str) and "discord_id" in m
            and (m["discord_id"] is None or (isinstance(m["discord_id"], str) and m["discord_id"].isdigit())))


def team_ok(key: object, t: object) -> bool:
    return (isinstance(key, str) and key.isdigit() and isinstance(t, dict) and isinstance(t.get("name"), str)
            and rank_ok(t.get("rank")) and (t.get("elo") is None or number(t["elo"])) and rank_ok(t.get("wins"))
            and isinstance(t.get("dev"), bool) and isinstance(t.get("has_bot"), bool)
            and all(k not in t or moment(t[k]) for k in ("members_at", "tried_at", "first_seen", "name_since"))
            and ("previous" not in t or (isinstance(t["previous"], list) and len(t["previous"]) <= PREVIOUS
                                         and all(isinstance(n, str) for n in t["previous"])))
            and ("members" not in t or (isinstance(t["members"], list) and all(map(member_ok, t["members"])))))


def entry_ok(e: object) -> bool:
    return (isinstance(e, dict) and whole(e.get("team_id")) and isinstance(e.get("team"), str)
            and rank_ok(e.get("rank")) and isinstance(e.get("dev"), bool)
            and (e.get("elo") is None or number(e["elo"])) and rank_ok(e.get("wins")))


# What is wrong with a teams.json record's shape, or None when every field the loop reads is of its type.
def problem(record: object) -> str | None:
    if not isinstance(record, dict):
        return "not an object"
    for key in ("attempt_at", "fetched_at", "failing_since"):
        if key in record and not moment(record[key]):
            return f"{key} is not a time"
    for key in ("error", "pages_error"):
        if record.get(key) is not None and not isinstance(record[key], str):
            return f"{key} is not text"
    teams = record.get("teams", {})
    if not isinstance(teams, dict) or not all(team_ok(k, t) for k, t in teams.items()):
        return "a team entry has the wrong shape"
    for index in ("accounts", "usernames"):
        found = record.get(index, {})
        if not isinstance(found, dict) or not all(isinstance(k, str) and entry_ok(e) for k, e in found.items()):
            return f"{index} has the wrong shape"
    return None


# teams.json and what was wrong with it: ({}, None) when there is none yet, ({}, why) when it can't be read or has
# the wrong shape, so every reader gets either a well-formed record or an empty one.
def load(state: Path) -> tuple[dict, str | None]:
    try:
        record = json.loads(path_in(state).read_text())
    except FileNotFoundError:
        return {}, None
    except (OSError, ValueError) as error:
        return {}, f"{type(error).__name__}: {error}"[:200]
    wrong = problem(record)
    return ({}, wrong) if wrong else (record, None)


def read(state: Path) -> dict:
    return load(state)[0]


# A site member as teams.json keeps them: their username and the Discord account id in their avatar URL, if any.
def member(entry: dict) -> dict:
    found = DISCORD_AVATAR.match(str(entry.get("avatarUrl") or ""))
    return {"username": str(entry.get("username") or ""), "discord_id": found[1] if found else None}


def jsonl_rows(path: Path) -> list[dict]:
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


# {team id: the names it had in the loop's saved data, latest first}: the study's and the scout's replay indexes (each
# game's two teams as named when fetched, `fetchedAt`) and study-history.jsonl (the leaders and dev team each study
# listed, `at`). Both times are UTC ISO strings, compared to the second. Never raises; unreadable rows are skipped.
def seen_names(state: Path) -> dict[str, list[str]]:
    latest: dict[str, dict[str, str]] = {}

    def saw(team: object, name: object, at: object) -> None:
        if whole(team) and isinstance(name, str) and name and isinstance(at, str):
            names = latest.setdefault(str(team), {})
            names[name] = max(names.get(name, ""), at[:19])

    for index in ("study-replays", "scout-replays"):
        for row in jsonl_rows(state / index / "index.jsonl"):
            for side in "AB":
                saw(row.get(f"team{side}Id"), row.get(f"team{side}Name"), row.get("fetchedAt"))
    for row in jsonl_rows(state / "study-history.jsonl"):
        leaders = row.get("leaders") if isinstance(row.get("leaders"), list) else []
        for team in [*leaders, row.get("dev")]:
            if isinstance(team, dict):
                saw(team.get("id"), team.get("name"), row.get("at"))
    return {key: sorted(names, key=lambda n: names[n], reverse=True) for key, names in latest.items()}


# The teams of `board` (the /leaderboard rows) with this read's name, rank, elo, wins and dev flag, keeping each team's
# member list from `old` unless its row lists members itself, and its name history: when it was first listed, since
# when it has had this name (only when known: a rename seen here, or a team new to a read after the first), and its
# PREVIOUS older names, latest first: a rename seen here, then the names already kept, then those in `seen`
# (seen_names) the team no longer has. Teams no longer on the leaderboard (their name history too), and rows without
# an integer id, are dropped; a rank, elo or wins of another type is kept as none, so every entry has team_ok's shape.
def merged(old: dict, board: list[dict], now: float, seen: dict[str, list[str]] | None = None) -> dict:
    teams = {}
    for row in board:
        if not isinstance(row, dict) or not whole(row.get("id")) or row["id"] < 0:
            continue
        key = str(row["id"])
        before, name = old.get(key, {}), str(row.get("name") or "")
        renamed = bool(before.get("name")) and before["name"] != name
        older = [*([before["name"]] if renamed else []), *before.get("previous", []), *(seen or {}).get(key, [])]
        history = {"first_seen": before.get("first_seen", now),
                   "previous": list(dict.fromkeys(n for n in older if n != name))[:PREVIOUS]}
        if renamed or (old and not before):
            history["name_since"] = now
        team = {**before, **history, "name": name,
                "rank": row.get("rank") if rank_ok(row.get("rank")) else None,
                "elo": row["elo"] if number(row.get("elo")) else None,
                "wins": row.get("wins") if rank_ok(row.get("wins")) else None,
                "dev": bool(row.get("dev")), "has_bot": bool(row.get("hasBot"))}
        if isinstance(row.get("members"), list):
            team.update(members=[member(m) for m in row["members"] if isinstance(m, dict)], members_at=now,
                        tried_at=now, members_from="leaderboard")
        teams[key] = team
    return teams


# The teams last tried (fetched or failed) over MEMBERS_EVERY ago or never, ranked teams with a bot first by rank,
# then the rest (no bot or no rank: their posters matter least).
def due(teams: dict, now: float) -> list[str]:
    stale = [key for key, team in teams.items()
             if now - team.get("tried_at", team.get("members_at", 0)) >= MEMBERS_EVERY]
    return sorted(stale, key=lambda key: (not (teams[key]["has_bot"] and teams[key]["rank"]),
                                          teams[key]["rank"] or 0, int(key)))


# {Discord account id: team} and {lowercased site username: team} for members without an account id. A person on two
# teams keeps the better-ranked one. A team not of team_ok's shape is skipped (load and merged never give one), so
# this, and the save it runs in, never raise on a bad entry.
def indexes(teams: dict) -> tuple[dict, dict]:
    accounts: dict[str, dict] = {}
    usernames: dict[str, dict] = {}
    good = {key: team for key, team in teams.items() if team_ok(key, team)} if isinstance(teams, dict) else {}
    order = sorted(good, key=lambda key: (good[key]["rank"] is None, good[key]["rank"] or 0, int(key)))
    for key in order:
        team = good[key]
        entry = {"team_id": int(key), "team": team["name"], "rank": team["rank"], "elo": team.get("elo"),
                 "wins": team.get("wins"), "dev": team["dev"]}
        for m in team.get("members", []):
            if m["discord_id"]:
                accounts.setdefault(m["discord_id"], entry)
            elif m["username"]:
                usernames.setdefault(m["username"].lower(), entry)
    return accounts, usernames


def save(state: Path, record: dict) -> None:
    path = path_in(state)
    accounts, usernames = indexes(record.get("teams", {}))
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({**record, "accounts": accounts, "usernames": usernames}, ensure_ascii=False,
                                    indent=1) + "\n")
    temporary.replace(path)


# The team ids whose renames alert: ranks 1 to RENAME_RANKS in either read, dev teams, and the opponents of our
# current submission's games in online.json (none when it can't be read).
def tracked(state: Path, old: dict, new: dict) -> set[str]:
    try:
        online = json.loads((state / "online.json").read_text())
        played = {str(g.get("opponent")) for g in online.get("games", [])  # its play group's (online.same_play)
                  if g.get("submission_id") in (online.get("play_group") or [online.get("submission_id")])}
    except (OSError, ValueError, AttributeError):
        played = set()
    top = {key for teams in (old, new) for key, t in teams.items()
           if isinstance(t, dict) and whole(t.get("rank")) and 0 < t["rank"] <= RENAME_RANKS}
    return top | played | {key for key, t in new.items() if t.get("dev")}


def place(team: dict) -> str:
    return f"#{team['rank']}" if team.get("rank") else "unranked"


def top(team: object) -> bool:
    return isinstance(team, dict) and whole(team.get("rank")) and 0 < team["rank"] <= ALERT_RANKS


def elo(team: dict) -> str:
    return f" (Elo {team['elo']:.0f})" if team.get("elo") is not None else ""


# host-alerts.log lines for what changed between two reads' teams: 'teams: team 306 renamed "Vibing++" -> "Cutlery"'
# for each team in `watched` (tracked), and one line for the top when rank 1 changed hands or a team entered or left
# ranks 1 to ALERT_RANKS (joined the read there, or left the read from there): 'teams: top 5 changed: team 91 "SSS"
# #7 -> #5 (Elo 2040); team 249 "Team SKKU" #5 -> #6 (Elo 2021)'. A swap inside the top says nothing. None after a
# first read (`old` empty). Names are quoted_name'd, so no name can start a forged line.
def news(old: dict, new: dict, watched: set[str]) -> list[str]:
    lines, moves = [], []
    for key in sorted(new, key=lambda k: (new[k].get("rank") or 10 ** 9, int(k))):
        team, before = new[key], old.get(key)
        label = f"team {key} {quoted_name(team['name'])}"
        if not isinstance(before, dict):
            if old and top(team):
                moves.append(f"{label} new at {place(team)}{elo(team)}")
            continue
        if key in watched and before.get("name") and before["name"] != team["name"]:
            lines.append(f"teams: team {key} renamed {quoted_name(before['name'])} -> {quoted_name(team['name'])} "
                         f"({place(team)}): a record naming it by the old name is stale; teams.json keeps both")
        if (team.get("rank") == 1 and before.get("rank") != 1) or top(team) != top(before):
            moves.append(f"{label} {place(before)} -> {place(team)}{elo(team)}")
    for key in sorted(set(old) - set(new), key=int):
        if top(old[key]):
            moves.append(f"team {key} {quoted_name(old[key]['name'])} {place(old[key])} -> off the leaderboard")
    return lines + ([f"teams: top {ALERT_RANKS} changed: {'; '.join(moves)}"] if moves else [])


# The name team `team_id` has on the latest leaderboard read in `record` (teams.json), or `fallback` (a replay's name,
# from when it was played) when that read doesn't list it.
def current_name(record: dict, team_id: object, fallback: object) -> object:
    team = (record.get("teams") or {}).get(str(team_id))
    return team["name"] if isinstance(team, dict) and team.get("name") else fallback


# ', formerly "Vibing++"' for a label of team `team_id` named `name`: its older names in `record`, quoted; "" without.
def formerly(record: dict, team_id: object, name: object) -> str:
    team = (record.get("teams") or {}).get(str(team_id))
    older = [n for n in team.get("previous", []) if n != name] if isinstance(team, dict) else []
    return f", formerly {', '.join(map(quoted_name, older))}" if older else ""


RENAMED = 40  # renamed teams latest.md lists at most, best-ranked first


# latest.md's line mapping old team names to ids, for a post that names a team by one: the RENAMED best-ranked teams
# with older names, or "" when none has any.
def renames(record: dict) -> str:
    teams = record.get("teams") or {}
    renamed = sorted((k for k, t in teams.items() if isinstance(t, dict) and t.get("previous")),
                     key=lambda k: (teams[k].get("rank") or 10 ** 9, int(k)))
    if not renamed:
        return ""
    shown = [f"team {k} {quoted_name(teams[k]['name'])} ({place(teams[k])}){formerly(record, k, teams[k]['name'])}"
             for k in renamed[:RENAMED]]
    more = f"; {len(renamed) - RENAMED} more in teams.json" if len(renamed) > RENAMED else ""
    return f"Renamed teams (a post may use an old name): {'; '.join(shown)}{more}."


# Refreshes teams.json when its last attempt is `every` seconds old (see the module docstring). `get` is ladder.get,
# `stop` says to stop between team pages (a stop marker). Writes `news` to host-alerts.log after a good read that
# follows an earlier one. Returns the record, or None when it wasn't due.
def refresh(state: Path, now: float | None = None, get: Callable[[str], object] | None = None,
            sleep: Callable[[float], None] = time.sleep, stop: Callable[[], bool] = lambda: False,
            every: float = EVERY) -> dict | None:
    now = time.time() if now is None else now
    if get is None:
        import ladder
        get = ladder.get
    path_in(state).parent.mkdir(parents=True, exist_ok=True)
    with locked(path_in(state)):
        record, unreadable = load(state)
        if now - record.get("attempt_at", 0) < every:
            return None
        kept = ("fetched_at", "teams", "failing_since", "pages_error")
        record = {key: record[key] for key in kept if key in record}
        record.update(attempt_at=now, error=None, failed=[])
        try:
            import ladder
            board = ladder.items(get("/leaderboard"), "teams")
            old = record.get("teams", {})
            teams = merged(old, board, now, seen_names(state))
            if not teams:
                raise ValueError("the leaderboard listed no teams")
            record.update(teams=teams, fetched_at=now)
            save(state, record)
            for line in news(old, teams, tracked(state, old, teams)):
                alert(state, line)
            tried = 0
            for key in due(teams, now)[:FETCHES]:
                if stop():
                    break
                sleep(PACE)
                tried += 1
                teams[key]["tried_at"] = now
                try:
                    payload = get(f"/teams/{key}")
                    listed = payload["team"]["members"] if isinstance(payload, dict) else None
                    if not isinstance(listed, list):
                        raise ValueError("no member list in the team page")
                    teams[key].update(members=[member(m) for m in listed], members_at=now, members_from="team page")
                except (Exception, SystemExit) as error:
                    record["failed"].append(f"team {key}: {type(error).__name__}: {error}"[:200])
                if tried % SAVE_EVERY == 0:
                    save(state, record)
            # A run that tried no pages (none due: the failed ones wait a day) leaves the last verdict standing while
            # a team still waits on a failed page, so pages that all failed stay an error until a later run's
            # fetches mostly succeed, or the leaderboard lists those teams' members itself.
            waiting = any(t.get("tried_at", 0) > t.get("members_at", -1) for t in teams.values())
            if 2 * len(record["failed"]) > tried:
                record["pages_error"] = (f"{len(record['failed'])} of {tried} team pages failed, the first: "
                                         f"{record['failed'][0]}")
            elif tried or not waiting:
                record.pop("pages_error", None)
            record["error"] = record.get("pages_error")
        except (Exception, SystemExit) as error:  # SystemExit: ladder.api_key exits without a key
            record["error"] = f"{type(error).__name__}: {error}"[:500]
        if record["error"]:
            record["failing_since"] = record.get("failing_since", now)
        else:
            record.pop("failing_since", None)
        if unreadable:  # recorded, but not a failing refresh: this one rebuilt it
            record["error"] = "; ".join(filter(None, [record["error"], f"teams.json was unreadable ({unreadable}), "
                                                                       "so it was rebuilt from the leaderboard"]))
        save(state, record)
        return record


# What latest.md shows after a poster's display name: "(team 859 "over|yonder", #180, Elo 1518, 166 wins)": its wins
# from the leaderboard (its rows give no losses, and team pages, which do, are only read for teams whose row lists no
# members), since everyone started at Elo 1500 and a rank or Elo from a few games says little. With ", dev team" for
# an organisers' dev team and ", by username" when only the site username matched (weaker: anyone can pick one), or
# "(no team found)". The team name is quoted like the display name (quoted_name), since anyone can pick one. A webhook
# has no account, so it is never matched by the name it posts under. Never raises, whatever `teams` holds.
def team_label(message: dict, teams: dict) -> str:
    accounts, usernames = teams.get("accounts"), teams.get("usernames")
    if not teams.get("teams") or not isinstance(accounts, dict) or not isinstance(usernames, dict):
        return "(team unknown: no team list read yet)"
    if message.get("kind") == "webhook":
        return "(no team found)"
    author, username, how = message.get("author_id"), message.get("username"), ""
    entry = accounts.get(author) if isinstance(author, str) else None
    if entry is None and isinstance(username, str) and username:
        entry, how = usernames.get(username.lower()), ", by username"
    if not entry_ok(entry):
        return "(no team found)"
    rank = f"#{entry['rank']}" if entry["rank"] else "unranked"
    elo = f", Elo {entry['elo']:.0f}" if entry.get("elo") is not None else ""
    played = f", {entry['wins']} wins" if entry.get("wins") is not None else ""
    dev = ", dev team" if entry["dev"] else ""
    return f"(team {entry['team_id']} {quoted_name(entry['team'])}, {rank}{elo}{played}{dev}{how})"


# Our own place on the ladder for latest.md's first line: "our team 157 is #17 of 457 ranked", or why it isn't known.
def standing(teams: dict) -> str:
    listed = teams.get("teams")
    if not isinstance(listed, dict) or not listed:
        return "our rank unknown: no team list read yet"
    ours = listed.get(OUR_TEAM)
    if not isinstance(ours, dict):
        return f"our team {OUR_TEAM} is not on the leaderboard"
    ranked = sum(isinstance(t, dict) and bool(t.get("rank")) for t in listed.values())
    if not ours.get("rank"):
        return f"our team {OUR_TEAM} is unranked ({ranked} ranked)"
    return f"our team {OUR_TEAM} is #{ours['rank']} of {ranked} ranked"


def main() -> None:
    state = loop_home()
    record = refresh(state, every=0)
    teams = record.get("teams", {})
    accounts, usernames = indexes(teams)
    print(f"teams: {len(teams)} teams, {len(accounts)} Discord accounts, {len(usernames)} usernames without one, "
          f"{len(record['failed'])} team pages failed; {standing(record)}; error: {record['error']}", file=sys.stderr)


if __name__ == "__main__":
    main()
