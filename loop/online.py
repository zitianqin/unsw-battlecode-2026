"""Map-focused scrims and version-bound replay evidence; no model calls.

`cycle` (the upkeep tick) collects our finished online games, writes online.json's reports and requests an unranked
sweep of a non-dev team at most every REQUEST_EVERY, within GAMES_AN_HOUR. Battles against the organisers' dev teams
have their own hourly allowance on the site, apart from the other teams' (sig, 2026-09-27 ~22:57; uncounted from that
morning until then), so they are played for as long as each submission is live, paced to fit DEV_GAMES_AN_HOUR
(dev_due): `dev_round`, run by each cycle and by `dev_poll` every DEV_POLL_EVERY between cycles (`polling`, in
maintenance.checking), sends the next dev battle when one is due: first to a team due a probe (not played for
DEV_PROBE_S, or once a version after a loss to a team we beat heavily; never two probes in a row), else to the team
whose games lag its usefulness (dev_weight, near 0 for a team we always beat: sig 2026-09-27, keep only the games that
inform, since every dev replay is public) most, at most DEV_IN_FLIGHT playing and none while our queued ones wait
behind more than MAX_AHEAD. Every dev result is kept; replays for the first DEV_REPLAYS per version
per team and for up to DEV_LOSS_REPLAYS of its losses and draws.
Dev games are kept apart from ladder and autoscrim games wherever our online games are read (against_dev).
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import math
import re
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import zipfile
from collections import Counter, defaultdict
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import ladder
import ladder_maps
import meta
import official
import prune
from chance import TRIGGER_P, rate_ci, rates_p, sign_p  # the loop's one chance helper (MISSES 34)
from meta import locked


def save(path: Path, value: object) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def read(path: Path, default):
    return json.loads(path.read_text()) if path.exists() else default


# Our side of a finished game (FINISHED; a game's record, GET /battles/:id), or None: "side", and "our_submission"
# when the record names it, else None (records stopped naming submissions on 2026-09-28, API_GONE; collect_games then
# reads ours from the replay or our request row, our_submission).
def match_for_team(detail: dict, team_id: int) -> dict | None:
    match = detail.get("match", {})
    if match.get("status") not in FINISHED:
        return None
    for side in ("A", "B"):
        if match.get(f"team{side}Id") == team_id:
            return {**match, "side": side, "our_submission": match.get(f"submission{side}Id")}
    return None


# The fields of GET /battles/:id that the site dropped at about 19:30 AEST on 2026-09-28, even from older battles
# (MISSES 77): each game's submissions (submissionAId, submissionBId), replayKey and requestedBy. Since then our
# submission comes from our_submission and the opponent's is unknown (None): readers keyed on an opponent version
# (compare_versions, dev_compare, opponent_versions, the study's version_games) treat None as unknown, never as one
# version.
API_GONE = ("submissionAId", "submissionBId", "replayKey", "requestedBy")
# The fields of a completed battle's detail the loop reads (collect_games, battle_result, settled_battles, the dev
# timing): one missing from a detail read is alerted once (api_news), and a game the cycle can't record shows in
# online.json's "unrecorded" (unrecorded_games, health.online_recording).
API_MATCH_FIELDS = ("id", "teamAId", "teamBId", "mapId", "ranked", "status", "winner", "requestedAt")
API_GAME_FIELDS = ("id", "status", "winner", "mapName")


# Each game id the loop requested -> the submission it requested as (match-requests.json's rows: `ids`,
# `submission_id`). A battle plays the submission live when it was requested: on 2026-09-28 every one of the 12,266
# recorded games the loop had requested was filed under its request row's submission by the site's own field.
def requested_submissions(state: Path) -> dict[int, int]:
    rows = read(state / "match-requests.json", [])
    return {gid: row["submission_id"] for row in rows if isinstance(row, dict) and row.get("submission_id")
            for gid in row.get("ids") or []}


# Our submission id as the replay names it: the site writes the viewing team's submission id as its side's bot name
# (botA or botB) and leaves the other side's blank (every one of about 10,000 saved online replays to 2026-09-28,
# and blank on both sides in the leader study's), or None when the file can't be read, names none, or names one not
# in `ours` (our submissions the loop knows of: a bot name is whatever its uploader typed, so a stranger's is never
# trusted as ours).
def replay_submission(path: Path, side: str, ours: set[int]) -> int | None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import replays

    try:
        name = getattr(replays.load_replay(path), f"bot{side}")
    except Exception:
        return None
    if not isinstance(name, str) or not re.fullmatch(r"[0-9]+", name):  # ASCII digits: "²".isdigit() is True
        return None
    return int(name) if int(name) in ours else None


# Our submission for game `gid` of `match` (match_for_team's) without a replay: the record's own field, else our
# request row's (requested_submissions), with where it came from; (None, None) when neither names it.
def our_submission(match: dict, gid: int, requested: dict[int, int]) -> tuple[int | None, str | None]:
    if match.get("our_submission"):
        return match["our_submission"], "api"
    if gid in requested:
        return requested[gid], "request"
    return None, None


# Fields of API_MATCH_FIELDS and API_GAME_FIELDS ("match.<field>", "games.<field>") that some completed battle among
# `details` (GET /battles/:id, as read this cycle) lacks, sorted.
def missing_fields(details: list[dict]) -> list[str]:
    missing = set()
    for detail in details:
        match = detail.get("match") or {}
        if match.get("status") not in FINISHED:
            continue
        missing |= {f"match.{f}" for f in API_MATCH_FIELDS if f not in match}
        for game in detail.get("games") or []:
            if game.get("status") in FINISHED:
                missing |= {f"games.{f}" for f in API_GAME_FIELDS if f not in game}
    return sorted(missing)


# online.json's "api_missing" for this cycle's `missing` (missing_fields) and the alert lines: one when a set of
# missing fields first appears or changes, none while it holds (health.online_recording repeats it every health.REPEAT).
def api_news(old: dict | None, missing: list[str], now: float) -> tuple[dict | None, list[str]]:
    if not missing:
        return None, []
    if old and old.get("fields") == missing:
        return old, []
    return {"fields": missing, "since": now}, [
        f"online: the site's battle details now lack {', '.join(missing)}, which the online cycle reads: check "
        "every reader of them (online.API_MATCH_FIELDS; MISSES 77) and that games are still recorded"]


# The finished games (FINISHED) of this cycle's `details` (battle id -> detail: the listed battles and those carried
# past the list, owed_battles) that no index records (`recorded`, recorded_ids), for online.json's "unrecorded"
# (health.online_recording alerts on it): how many, and when the oldest's battle finished (its row's `at` in
# `battles`, else its detail's completedAt), or None when every one is recorded.
def unrecorded_games(battles: list[dict], details: dict[int, dict], recorded: set[int]) -> dict | None:
    at = {b["id"]: b.get("at") for b in battles}
    missed = [(str(at.get(bid) or (detail.get("match") or {}).get("completedAt") or ""), g["id"])
              for bid, detail in details.items() for g in detail.get("games") or []
              if g.get("status") in FINISHED and g.get("id") not in recorded]
    if not missed:
        return None
    return {"games": len(missed), "oldest": min(missed)[0] or None, "example": min(missed)[1]}


def digest(files: dict[str, bytes]) -> str:
    result = hashlib.sha256()
    for name, content in sorted(files.items()):
        result.update(name.encode() + b"\0" + hashlib.sha256(content).digest())
    return result.hexdigest()


def archive_digest(raw: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names = [entry.filename for entry in archive.infolist() if not entry.is_dir()]
        if len(names) != len(set(names)):
            raise ValueError("duplicate archive paths")
        return digest({name: archive.read(name) for name in names})


def current_games(report: dict, submission_id: int) -> list[dict]:
    return report.get("games", []) if report.get("submission_id") == submission_id else []


GAMES_AN_HOUR = 50  # games against non-dev teams the loop requests in a rolling hour; the site allows 60 (api.md)
# Dev battles, how they are scheduled, kept and read (moved from LOOP.md: Dev battles, 2026-09-28):
# Dev battles play while a submission is live, paced, only while they inform (sig, 2026-09-27, his words at
# `online.DEV_GAMES_AN_HOUR`: every dev battle is public, so each dev game is another public replay of our live bot).
# The organisers' dev teams (`"dev": true` on `/leaderboard`, not their names; read live each cycle, those with a bot,
# `online.dev_opponents`) are left out of autoscrims and competitions, are "frontier with features stripped", and
# their battles have their own 60 games an hour on the site, apart from the other teams', of which the loop requests
# at most `online.DEV_GAMES_AN_HOUR` a rolling hour (reserved before POST, as the other pool's). So for as long as a
# submission is live, the loop sends one unranked dev battle, on every active map (one game per distinct map), each
# `online.dev_pace` (the allowance spread evenly over the hour) after its last dev request, and only while the rolling
# hour has room for it (`online.dev_due`), so a new version starts playing within minutes rather than inheriting a
# spent hour; the poll skips its API reads until then and says so in dev-poll.json, and the stall alert
# (`health.dev_battles`) counts from when the next was due. online.json's dev block and online.md show the allowance
# left and when the next is due. Each goes to a dev team with none pending (the site's one pending battle per
# opponent; a 409 means still pending, spends nothing and tries the next team in the order, which is returned whole so
# a team stuck on the site can't block the rest). A team due a probe goes first, the longest unplayed first, unless
# the latest dev request the site didn't refuse was a probe (`online.after_probe`, whose comment has the case), so
# probes due together alternate with share picks. A team is due a probe with no dev battle requested for
# `online.DEV_PROBE_S` (by any version of ours, `online.last_dev_request`): that shows whether its bot changed (its
# dev submission is read from our latest game against it) and is how a team we always beat is played at all. It is
# also due once a version (`online.loss_probe_due`) after that version loses to it while our pooled 95% lower bound
# against it is at least `online.DEV_HEAVY`: one loss barely moves a lopsided share, so a regression would wait for
# the next daily probe. Otherwise the one whose games this version, plus one battle's worth, are fewest per unit of
# measured usefulness (`online.dev_weight`, 4p(1−p) of our score over the latest `online.DEV_WEIGHT_GAMES` games
# against its current dev submission, every version of ours and pool period: a lopsided record gets a small share and
# a new dev submission starts again at 1), so the informative teams play first from a version's start and take most
# battles (sig's "ok go" and the measured shares: the comments at `online.DEV_PROBE_S` and `online.DEV_WEIGHT_GAMES`).
# At most `online.DEV_IN_FLIGHT` play at once, and it holds new ones back while any of our queued ones has more than
# `online.MAX_AHEAD` battles ahead of it (the game servers are shared; a live battle whose games are next or playing,
# `wait.ahead` 0, isn't queued, `online.waiting`). The upkeep cycle and a poll between cycles (`online.dev_poll`,
# every `online.DEV_POLL_EVERY`) send them; both stay near half the API's 120 requests a minute (`online.DEV_EVERY`'s
# comment). The poll sends only while publishing allows matches (`online.matches_allowed`, the tick's gate for the
# cycle) and on a dev team list and map pool the cycle read within `online.DEV_FRESH`, and a poll that sends nothing
# says why in dev-poll.json (`online.poll_skip`). A 429 stops that pass of reads or requests, spends nothing and holds
# no team, and every call in every loop process waits out its Retry-After (`ladder.quiet`, `ladder.QUIET_FILE`). Every
# result is kept, the cycle its battle's detail shows it finished (`online.battle_result`); replays for the first
# `online.DEV_REPLAYS` per version per dev team, whatever the result, so that sample stays unbiased, and for up to
# `online.DEV_LOSS_REPLAYS` of its losses and draws beyond it (marked `"kept": "loss"`, never in the sample, even
# while owed: `online.LOSS_REPLAYS_PER_CYCLE`), the rest as API results in the version's index;
# `prune.old_dev_replays` keeps two versions' replays, then deletes only wins, and not while an online alert on their
# comparison holds them (`online.hold_replays`, `prune.HOLD_S`): every loss's and draw's replay stays, and the
# version's dev rows are marked `"dev_wins_pruned"` first (`prune.mark_dev_rows`: its files are no sample then, its
# summaries still are, `online.file_selected`; the bounds: those constants' comments and prune's docstring). Every
# reader of our online games keeps dev games apart (`online.against_dev`): online.md's compare (and so the version
# alert and `online.raise_rechecks`), per-map rows,
# recent games and round-500 section (which gives each side's king at r396 and its meals and split-offs after, for
# losses and wins apart, M52, and leaves out replays kept because we lost), study.md's block of our games, and the
# audit's and coders' samples, which list a few dev losses after the rest, labelled. study.md's dev benchmark block
# leaves out the loss replays, which would tilt its sample to the benchmark's wins. A rate, record or per-map or
# per-opponent comparison counts every game (`online.all_games`: the index's `results` and `games`), a mean over
# replay summaries the unmarked ones (`online.sample_games`), and one over replay files `online.file_selected`'s
# complement (MISSES 55). The audit's context.md gives each version's full record, and watch.py's tally and
# blunders.py name the result-chosen replays among their inputs. online.md's dev
# section shows each dev team separately, with its games this version and its share with the score and 95% lower bound
# behind it (labelled as pooled over every version and pool period, as `audit.dev_picks`' labels are), and the dev
# benchmark (the one named frontier, else the highest-rated, `online.dev_benchmark`) for every version: each record is
# one of our versions against one dev submission with its games and 95% interval (dev teams update their bot, so a
# record never pools two, nor two pool periods), and a map-matched compare with the previous version on each dev
# submission both played, with a sign test and its n (or a note that they share none): a report, never a gate.
# host-alerts.log gets a line when the benchmark changes or plays a new submission; `dev.timing` in online.json gives
# how long a dev battle takes from request to results and how many finished in the last hour. health.py alerts when no
# dev battle was requested for `health.DEV_STALL` past when one was due while a submission is live, when the API's
# winner disagrees with a replay's (`online.winner_check`), since dev games past the replay sample are scored from it,
# and when a dev team has answered every request with 409 for `health.DEV_STUCK`, its latest within
# `health.stuck_recent` (two paces and cycles: a stuck team at the front of the order is tried every round, but one
# due a probe, while other probes go in its place, only every other round), a battle stuck on the site
# (`online.stuck_teams`).
#
# Games against dev teams the loop requests in a rolling hour: the site gave them a 60 an hour of their own, apart from
# the other teams' (sig, 2026-09-27 ~22:57: "the limit they set is now but separate 60/h against dev teams (separate
# form the other pool against real teams)"); as GAMES_AN_HOUR does, 10 stay free for sig's own. Dev battles play for as
# long as a submission is live (sig, 2026-09-27: "leave dev games out of our cap ... after each submission as well we
# should do like a billion dev games"), but only the games that inform (about 16:00, "do whatever u recommend" to
# that, since every dev battle is public and each dev game is another public replay of our live bot: dev_weight). They go out evenly,
# one battle every dev_pace (720 s on 10 maps), not the hour's in one burst, so a new version plays within minutes.
DEV_GAMES_AN_HOUR = 50
REQUEST_EVERY = 600  # the loop's own gap between requests against non-dev teams, and between cycles without dev teams
# While dev teams are listed the cycle runs this often, so their games are collected about as fast as they finish
# (one battle of every map each dev_pace, each about two minutes). API calls, each 0.6 s after
# the last: a cycle makes at most about 110 (the battle list, the details of listed battles not yet settled, about
# ten, since a dev battle's results come from its detail and settle it at once (collect_games), at most
# CARRIED_PER_CYCLE of battles past the list, RESULTS_PER_CYCLE game records, mostly the ladder's few,
# REPLAYS_PER_CYCLE and LOSS_REPLAYS_PER_CYCLE downloads, /team, /maps, /leaderboard and /submissions twice), about
# 55 a minute at this period, and dev_poll about 15 a minute on the polls that read (only when a dev battle is due,
# poll_skip): some 70 a minute at most against the site's 120, and one
# /leaderboard read every DEV_EVERY against its 30 a minute. A 429 anyway stops the reads and every call waits out its
# Retry-After (ladder.quiet).
DEV_EVERY = 120
# Our newest battles each cycle reads (`GET /battles?limit=`), one call however many: some 20 hours of dev battles at
# dev_pace (40 minutes when they went back to back). One that leaves it with games unrecorded is read by id for up to
# BEHIND_S more (owed_battles), CARRIED_PER_CYCLE a cycle.
BATTLE_WINDOW = 100
# A new dev battle is requested only while none of our queued dev battles has more than this many battles ahead of it
# (the battle list's `wait.ahead`), and at most this many plus one of ours wait in the queue: the game servers are
# shared, and other teams' games should never wait long behind ours; dev_due is the other brake. A battle ahead of
# ours pauses new dev requests until it drains.
MAX_AHEAD = 2
# At most this many of our dev battles play at once, so the teams' shares (dev_weight) decide who plays rather than
# every dev team playing whenever it is free: about the five strong dev teams' worth (dev test 1 and 2, abasdasga,
# dev test 3 and ML benchmark, 1633-1939 on 2026-09-27). Since dev_pace (a battle every 720 s on 10 maps, each
# taking about 113 s) seldom more than one plays, so this and MAX_AHEAD bind only when the site's queue is slow.
DEV_IN_FLIGHT = 5
# Replays kept per version of ours per dev team; later games against it keep only their API result (winner, map,
# side, dev submission). 50 give the replay-based reports (study.md's dev block reads the latest 30,
# maintenance.DEV_KEEP; audits and compare's length facts) about as many games as a ladder opponent's, while a replay is
# 0.6-1 MB (online-replays/7676: 240 MB for 249 replays; 8023: 287 MB for 510) and a summary run, and scores need only
# the result. Once
# neither the live version nor the one before it, prune.old_dev_replays packs the wins among them (not while an
# alert holds them, hold_replays), keeping their summaries; losses and draws keep their replays.
DEV_REPLAYS = 50
# Beside that sample, the replay of every dev loss and draw, up to this many per version of ours per dev team (marked
# "kept": "loss", so readers wanting a sample leave them out): on 2026-09-27 we lost 15 of 1,012 games to the weak dev
# teams, 11 on Trauma, and none had a replay. So a version keeps at most DEV_REPLAYS + DEV_LOSS_REPLAYS replays a dev
# team (at the 0.6-1 MB a replay measured above, 50-90 MB a team, 0.8-1.3 GB a version at 14 dev teams;
# prune.old_dev_replays keeps two versions' worth, and every loss's and draw's replay after: 18-19 MB a version on
# 2026-09-28).
DEV_LOSS_REPLAYS = 40
# A dev team's dev submission is read from our latest game against it (dev_report), and a team we always beat has a
# share near 0 (dev_weight), so it would show a new bot of its own, or a regression of ours against it, only by
# chance. So a dev team with no dev battle requested for DEV_PROBE_S (last_dev_request, by any version of ours) goes
# ahead of the share order (dev_requests), and none goes unplayed for more than about a day: 14 teams take at most 14
# of the 120 battles a day at DEV_GAMES_AN_HOUR. A new dev submission then restarts its share at 1 (dev_report). No
# dev team changed submission in any saved index to 2026-09-27. A probe is a dev battle like any other: it waits for
# dev_due, and it is how the teams we always beat are played at all (the orchestrator's "probe the other ten only
# rarely", to which sig said "ok go", 2026-09-27; a 6-hourly probe would have been about 17 games an hour, a third of
# the allowance).
DEV_PROBE_S = 24 * 3600
# A dev team we beat heavily: our score's 95% lower bound, over every version and pool period (dev_report's `lower`),
# at least this. A loss to one this version gets it one extra probe (loss_probe_due), and audit.dev_picks watches
# those losses first.
DEV_HEAVY = 0.9
REPLAYS_PER_CYCLE = 10  # replays downloaded and summarised per cycle (each one a download and a summary run)
# Loss replays (DEV_LOSS_REPLAYS) downloaded per cycle besides those: each costs about 2 s (two calls 0.6 s apart, a
# 0.6-1 MB download and a 0.6 s summary run, measured 2026-09-27), so at most some 20 s more on a cycle whose reads
# and collection took 40 s (online.json, 16:30), well within DEV_FRESH, the age of the cycle's team list past which
# dev_poll sends nothing.
# 10 a cycle is 300 an hour, more than the losses a version can give (DEV_LOSS_REPLAYS a team); the rest wait, still
# marked as losses (collect_games' `owed`), so waiting never lets one into the sample.
LOSS_REPLAYS_PER_CYCLE = 10
RESULTS_PER_CYCLE = 60  # game records (`GET /battles/:id`) read per cycle: 1,800 an hour at DEV_EVERY
# Our latest games against a dev team's current submission, from every version of ours and pool period, that set its
# share (dev_weight): 200 wins of 200 give 0.02. With 30 (0.12 for 30 wins), and within the pool period, the ten teams
# we beat 0.9-1.00 had 39% of the shares (online.json, 2026-09-27 23:27). At 23:30 every dev team had 290 or more such
# games (loop/testdata/online-dev-shares-sample.json): the seven we beat in every one of the latest 200 got 0.02 each
# (0.5% of the shares each), and dev test 1, Square, abasdasga and dev test 2 0.61-0.99 (83% together). A drop still
# shows within about a day: dev test 8's share (0.24 at 0.94) roughly doubles once 60 of its 200 score 0.7.
DEV_WEIGHT_GAMES = 200
DEV_TIMING_ROWS = 20  # the latest finished dev battles whose request-to-results time online.json reports
PLAYING = ("pending", "queued", "running")  # a game with one of these statuses hasn't finished
# A game with one of these statuses has finished with a result and a replay: a draw's status is "draw", not
# "completed" (game 411875, 2026-09-27, whose record's winner is null), and until MISSES 83 every draw was skipped
# unrecorded and uncounted (no saved row among 17,000 was a draw).
FINISHED = ("completed", "draw")
# A battle whose games aren't all recorded is read by id each cycle after it leaves the list (owed_battles,
# online.json's "behind") until they are, or for this long after it was first seen unsettled: a game whose replay
# never comes, or a battle the site never plays, then stops costing a read a cycle, with one host-alerts.log line
# (online.json's "closed", "given up").
BEHIND_S = 3 * 86400
# Battles past the list (behind and requested_battles) read per cycle, oldest first: about the REPLAYS_PER_CYCLE
# their games can be recorded in, so a backlog of them costs a cycle at most some 6 s more of reads.
CARRIED_PER_CYCLE = 10


# Games the loop may still request against non-dev teams this rolling hour: GAMES_AN_HOUR less those reserved in the
# last hour. Rows marked "dev" (battles against the organisers' dev teams, from 2026-09-27) count only in their own
# pool, as on the site (remaining_dev_games).
def remaining_games(rows: list[dict], now: float) -> int:
    return max(0, GAMES_AN_HOUR - sum(r.get("games", 0) for r in rows if r["at"] > now - 3600 and not r.get("dev")))


# Games the loop may still request against dev teams this rolling hour: DEV_GAMES_AN_HOUR less the dev rows reserved in
# the last hour, a refused or unclear request spending its games as in remaining_games.
def remaining_dev_games(rows: list[dict], now: float) -> int:
    return max(0, DEV_GAMES_AN_HOUR - sum(r.get("games", 0) for r in rows if r["at"] > now - 3600 and r.get("dev")))


# Seconds between dev battles so a steady stream of them fits DEV_GAMES_AN_HOUR: one game per active map in `maps`,
# 720 s on 10 maps.
def dev_pace(maps: list[int]) -> float:
    return 3600 * len(maps) / DEV_GAMES_AN_HOUR


# When the next dev battle on `maps` may be requested, given the dev rows in `rows` (match-requests.json): dev_pace
# after our last dev request against any team (every row reserved, a refused or unclear one too), and not before the
# rolling hour leaves room for its games (remaining_dev_games). 0 when nothing holds it. dev_round sends one battle only
# once it has passed, poll_skip skips the poll's reads until then, and health.dev_battles times a stall from it.
def dev_due(rows: list[dict], maps: list[int]) -> float:
    dev = sorted((r for r in rows if r.get("dev")), key=lambda r: r["at"], reverse=True)
    due, games = (dev[0]["at"] + dev_pace(maps) if dev else 0), 0
    for row in dev:  # newest first: the first row whose games overflow the room must leave the hour
        games += row.get("games", 0)
        if games > DEV_GAMES_AN_HOUR - len(maps):
            return max(due, row["at"] + 3600)
    return due


# Whether a leaderboard team is one of the organisers' dev teams (see dev_benchmark).
def is_dev(team: dict) -> bool:
    return team.get("dev") is True


# The dev team ids online.json's report knows: the teams its `dev` block lists and the benchmark's.
def dev_ids(report: dict) -> set[int]:
    teams = {t["id"] for t in (report.get("dev") or {}).get("teams", [])}
    bench = ((report.get("benchmark") or {}).get("team") or {}).get("id")
    return teams | ({bench} if bench else set())


# Whether a saved game or API result was against a dev team: marked "dev" when collected (collect_games, from
# 2026-09-27), or against a team in `dev` (dev_ids; games collected before the mark). Readers of our online games keep
# these apart from ladder and autoscrim games, which a few hundred dev games an hour would otherwise swamp.
def against_dev(game: dict, dev: set[int]) -> bool:
    return bool(game.get("dev")) or game.get("opponent") in dev


# The dev teams we can play: flagged dev, with a bot (mustramallow and brggr are flagged but had none, 2026-09-27), not
# us, and taking unranked battles. Read from the live leaderboard every cycle, never a fixed list.
def dev_opponents(teams: list[dict], team_id: int) -> list[dict]:
    return [t for t in teams if is_dev(t) and t.get("hasBot") and t["id"] != team_id
            and t.get("settings", {}).get("unranked", True)]


# The organisers' dev teams carry "dev": true on the leaderboard (16 on 2026-09-26: "dev test 1 :P" to "dev test 10",
# also "ML benchmark", "Square", "sl" and others, while the non-dev team 391 is named "DEV"), so the flag decides, not
# the name. An Admin (Discord, 2026-09-24): "dev test 1 will become frontier", the others are "frontier with various
# 'features' stripped away", and dev teams are excluded from autoscrims and competitions. The benchmark is the dev team
# named frontier, else the highest-rated dev team with a bot: {id, name, elo, why}, or None without one.
def dev_benchmark(teams: list[dict]) -> dict | None:
    dev = [t for t in teams if is_dev(t) and t.get("hasBot")]
    named = [t for t in dev if "frontier" in str(t.get("name", "")).lower()]
    pool, why = (named, "named frontier") if named else (dev, "highest-rated dev team, none named frontier")
    if not pool:
        return None
    team = max(pool, key=lambda t: (t.get("elo", 0), -t["id"]))
    return {"id": team["id"], "name": team.get("name"), "elo": team.get("elo"), "why": why}


# Our latest games against each team, for the sweep's shares (sweep_weight): every version's ladder, autoscrim and
# sweep games (all_games, dev games apart) requested within SWEEP_WEIGHT_S of `now`, the newest SWEEP_WEIGHT_GAMES a
# team, as {team: [our score in each]}, oldest first. `games` are the live version's, as collect_games returned them.
# Reads every index (62 of them, 85 MB, in 0.4 s on 2026-09-29).
def sweep_scores(state: Path, games: list[dict], dev: set[int], now: float) -> dict[int, list[float]]:
    rows: dict[int, dict] = {}
    for path in (state / "online-replays").glob("*/index.json"):
        rows.update((g["id"], g) for g in all_games(read(path, {})))
    rows.update((g["id"], g) for g in games)
    by_team: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for game in rows.values():
        at = moment(game.get("requested_at"))
        if at is not None and at > now - SWEEP_WEIGHT_S and not against_dev(game, dev):
            by_team[game.get("opponent")].append((at, game_score(game)))
    return {team: [score for _, score in sorted(found)[-SWEEP_WEIGHT_GAMES:]] for team, found in by_team.items()}


# A non-dev team's share of the sweep (sig's "teams that still beat us sometimes", as dev battles do: dev_weight) is
# dev_weight over our latest games against it (sweep_scores), with 2 games at the score the two ratings predict
# (expected_score) in place of dev_weight's level ones, so a team never played starts at what its rating says: 0.26
# for one 450 below us (CheeseTub, 1497 to our 1993 on 2026-09-29: 0.21, and 0.008 after 50 straight wins).
# SWEEP_WEIGHT_S keeps the games recent (teams upload, and the site names no versions, MISSES 77), so a team we
# stopped playing returns to its rating's share within two days. Measured on the loop's 1,981 sweep games requested
# 2026-09-27 03:23 to 09-29 03:04 UTC (match-requests.json's rows): 132 teams, 41 of them at a score of 0.9 or more
# taking 447 games (23%); team 106 (CheeseTub, rank 259) 56 games, all won, and 192 over 6824-10115.
SWEEP_WEIGHT_GAMES = 50
SWEEP_WEIGHT_S = 2 * 86400
# The previous version's opponents are replayed first only while their share is at least this (our score 0.15-0.85
# with the prior): a before/after pair of two wins says nothing.
SWEEP_PAIR_WEIGHT = 0.5


# Our expected score a game against a team rated `theirs`, ours `ours` (Elo's logistic at 400: 4.5 of 5 at 400 points
# ahead, docs/unsw/elo.md).
def expected_score(ours: float, theirs: float) -> float:
    return 1 / (1 + 10 ** ((theirs - ours) / 400))


def sweep_weight(scores: list[float], ours: float, theirs: float | None) -> float:
    return dev_weight(scores, expected_score(ours, ours if theirs is None else theirs))


# Every team a sweep may play (non-dev, with a bot, taking unranked battles, not us) and its share (sweep_weight).
# The cycle records the chosen team's in its match-requests.json row ("share", for health.sweep_shares).
def sweep_shares(teams: list[dict], team_id: int, elo: float,
                 scores: dict[int, list[float]] | None) -> dict[int, float]:
    return {t["id"]: sweep_weight((scores or {}).get(t["id"], []), elo, t.get("elo")) for t in teams
            if t["id"] != team_id and t.get("hasBot") and not is_dev(t) and t.get("settings", {}).get("unranked", True)}


# The team ids sig wants swept first (sig, 2026-10-01: Stockfish, 206, first), in order, from
# `<loop home>/online-targets.json` ({"teams": [206, ...], "until": "<ISO time>"}); the cycle passes them to
# choose_match. Empty when the file is missing, unreadable or past its `until`, so sweeps run as before.
def sweep_targets(state: Path, now: float) -> list[int]:
    try:
        spec = json.loads((state / "online-targets.json").read_text())
        if datetime.fromisoformat(spec["until"]).timestamp() <= now:
            return []
        return [int(t) for t in spec["teams"]]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def choose_match(teams: list[dict], maps: list[dict], team_id: int, elo: float, pending: set[int],
                 rows: list[dict], submission_id: int, now: float, games: list[dict] = (),
                 previous: list[dict] = (), scores: dict[int, list[float]] | None = None,
                 group: list[int] = (), targets: list[int] = ()) -> dict | None:
    """An unranked sweep of a non-dev team, within the hourly cap (remaining_games), on each active map. Ranked games
    come from the autoscrim draw, so every game we request is on a map we chose. Dev teams are left to dev_requests,
    within their own allowance. `previous` (the last version's unranked games) goes first: its opponents whose share
    (sweep_weight over `scores`, sweep_scores) is at least SWEEP_PAIR_WEIGHT, on the maps it played them in the current
    pool period, since the same opponent, map, start and pool period replay as a before/after pair (compare_versions);
    the most unpaired maps by share first. The site picks each game's layout (LAYOUT_CUTOFF), so a map stays wanted
    while one of the previous version's starts there has no game of this version and this version has requested it
    fewer times than there are such starts. Otherwise the team whose games this version, plus this battle's, are
    fewest per unit of share, as dev_requests orders dev teams (so informative teams play first and most), then the
    one requested least recently. "This version" is `submission_id` with the rest of its play group (`group`).
    Before all that, `targets` (sweep_targets) are swept in order: the first eligible one this version hasn't yet
    requested on every active map gets the maps it lacks."""
    ours = {submission_id, *group}
    budget = remaining_games(rows, now)
    share = {k: v for k, v in sweep_shares(teams, team_id, elo, scores).items() if k not in pending}
    candidates = [t for t in teams if t["id"] in share]
    if not budget or not candidates:
        return None
    active = {m["id"] for m in maps if m.get("active")}
    for target in targets:
        lacking = active - {m for row in rows if row.get("submission_id") in ours and row.get("opponent") == target
                            and row.get("status") != "rejected" for m in row.get("map_ids", [])}
        if target in share and lacking:
            return {"teamId": target, "ranked": False, "mapIds": sorted(lacking)[:budget]}
    # The previous version's starts against each opponent, map by map, less those this version has played there.
    period = period_at(now)
    starts: dict = defaultdict(lambda: defaultdict(set))
    for game in previous:
        if pairable(game) and pool_period(game) == period and start_key(game) is not None:  # all compare can pair
            starts[game["opponent"]][game["map_id"]].add(start_key(game))
    left = {opponent: {m: set(s) for m, s in maps_.items()} for opponent, maps_ in starts.items()}
    for game in games:
        if game.get("submission_id") in ours and game.get("opponent") in left:
            left[game["opponent"]].get(game.get("map_id"), set()).discard(start_key(game))
    # This version's requests of each map against each opponent. A request the server refused doesn't count, so it is
    # retried once its hour is up; one with an unclear outcome does, so no map is asked for past its starts.
    asked = Counter()
    for row in rows:
        if row.get("submission_id") in ours and row.get("status") != "rejected":
            asked.update((row.get("opponent"), m) for m in row.get("map_ids", []))
    unpaired = defaultdict(set)
    for opponent, maps_ in left.items():
        unpaired[opponent] = {m for m, s in maps_.items() if s and asked[opponent, m] < len(starts[opponent][m])}
    replays = [t for t in candidates if unpaired[t["id"]] & active and share[t["id"]] >= SWEEP_PAIR_WEIGHT]
    if replays:
        team = min(replays, key=lambda t: (-len(unpaired[t["id"]] & active) * share[t["id"]], t["id"]))
        return {"teamId": team["id"], "ranked": False, "mapIds": sorted(unpaired[team["id"]] & active)[:budget]}
    counts = requested_games(rows, submission_id, group)
    team = min(candidates, key=lambda t: ((counts.get(t["id"], 0) + len(active)) / share[t["id"]],
                                          max((r["at"] for r in rows if r.get("opponent") == t["id"]), default=0),
                                          abs(t.get("elo", elo) - elo), t["id"]))
    # When the budget can't fit every map, play the ones this version has played least against this opponent.
    coverage = defaultdict(int)
    for game in games:
        if game.get("opponent") == team["id"] and game.get("submission_id") in ours:
            coverage[game["map_id"]] += 1
    ids = sorted(active, key=lambda n: (coverage[n], n))
    return {"teamId": team["id"], "ranked": False, "mapIds": ids[:budget]} if ids else None


# A dev team's share of our dev battles: 4 p (1 - p), where p is our score in `scores` (our latest DEV_WEIGHT_GAMES
# games against its current submission) with one win and one loss added (two games at `prior`; sweep_weight's is the
# ratings' prediction). An unplayed team starts at 1, the most, a
# level record stays near 1, and a lopsided one falls towards 0 (200 wins of 200 give 0.02): a team we always beat, or
# that always beats us, says little about a change (sig, 2026-09-27).
def dev_weight(scores: list[float], prior: float = 0.5) -> float:
    p = (sum(scores) + 2 * prior) / (len(scores) + 2)
    return 4 * p * (1 - p)


# The battles ahead of one of ours in the site's queue (`wait.ahead` on the battle list's rows: a live battle's row
# carries {"ahead", "ms"} until its last game starts, a finished one has no `wait`, as the list read on 2026-09-27
# shows, loop/testdata/online-battles-sample.json), 0 without a `wait`.
def ahead(battle: dict) -> int:
    return int((battle.get("wait") or {}).get("ahead") or 0)


# The most battles ahead of any of `battles` (ahead), 0 with none queued.
def queue_ahead(battles: list[dict]) -> int:
    return max(map(ahead, battles), default=0)


# Whether one of our battles is queued behind others' (ahead > 0). A live battle keeps its `wait` until its last game
# starts, at `ahead` 0 once its games are next or playing (battle 339696 in the sample: 9 of 10 done, ahead 0), so a
# `wait` alone counted every live battle as queued and held dev battles to MAX_AHEAD + 1 in flight, not DEV_IN_FLIGHT.
# The list's `results` can't tell a game playing from one waiting (both "pending"), so `ahead` decides.
def waiting(battle: dict) -> bool:
    return ahead(battle) > 0


def dev_requests(dev: list[dict], weights: dict[int, float], counts: dict[int, int], queued: list[dict],
                 pending: set[int], maps: list[int], probes: dict[int, float] | None = None) -> list[dict]:
    """The dev battles that may be requested now, best first, each on every active map in `maps` (sig, 2026-09-27;
    the site plays one game per distinct map), to every dev team with no battle pending (the site allows one per
    opponent); dev_round sends the first the site takes, once one is due (dev_due). None while one of our queued dev
    battles has more than MAX_AHEAD battles ahead of it, or while MAX_AHEAD + 1 of ours wait behind others' (`queued`,
    our dev battles' rows that carry a `wait`, counted when `waiting`) or DEV_IN_FLIGHT of ours still play. The whole
    order is returned, not one battle per free slot: a team whose battle is stuck on the site answers 409 and never
    gains games, so it would stay first and, with one slot, block every dev battle. Teams due a probe (`probes`, team
    to its last dev request, dev_round's: none for DEV_PROBE_S) go first, the longest unplayed first; then the fewest
    games this version (`counts`) plus one battle's worth, per unit of share (`weights`, dev_weight), so each team's
    games grow with its share and at a version's start the informative teams play before the ones with no games yet
    but a small share (at about 5 battles an hour, a round of all 14 dev teams would take about 3 h, most of a
    version's life)."""
    probes = probes or {}
    playing = sum(t["id"] in pending for t in dev)
    if (not maps or queue_ahead(queued) > MAX_AHEAD or sum(map(waiting, queued)) > MAX_AHEAD
            or playing >= DEV_IN_FLIGHT):
        return []
    free = sorted((t for t in dev if t["id"] not in pending),
                  key=lambda t: (t["id"] not in probes, probes.get(t["id"], 0),
                                 (counts.get(t["id"], 0) + len(maps)) / weights.get(t["id"], 1.0),
                                 -weights.get(t["id"], 1.0), t["id"]))
    return [{"teamId": t["id"], "ranked": False, "mapIds": sorted(maps)} for t in free]


# Games this version (`submission_id` and the rest of its play group, `group`) has requested against each opponent,
# a refused request (which queued none) left out.
def requested_games(rows: list[dict], submission_id: int, group: list[int] = ()) -> dict[int, int]:
    counts: dict[int, int] = defaultdict(int)
    for row in rows:
        if row.get("submission_id") in {submission_id, *group} and row.get("status") != "rejected":
            counts[row.get("opponent")] += row.get("games", 0)
    return dict(counts)


# Marks each dev request in `rows` done (`done_at`) at the first cycle whose `details` show every one of its games
# finished; returns whether any changed. Its time from request to results is done_at - at, to within a cycle
# (DEV_EVERY), since the site gives no finish time.
def mark_done(rows: list[dict], details: list[dict], now: float) -> bool:
    status = {g.get("id"): g.get("status") for detail in details for g in detail.get("games", [])}
    changed = False
    for row in rows:
        ids = row.get("ids") or []
        if (row.get("dev") and ids and "done_at" not in row
                and all(i in status and status[i] not in PLAYING for i in ids)):
            row["done_at"] = now
            changed = True
    return changed


# How fast dev battles come back, for online.json and online.md: those finished in the last hour and their games,
# those requested in the last six hours still out, and the minutes from request to results (median and slowest) over
# the latest DEV_TIMING_ROWS finished.
def dev_timing(rows: list[dict], now: float) -> dict:
    done = sorted((r for r in rows if r.get("dev") and r.get("done_at")), key=lambda r: r["done_at"])
    minutes = sorted((r["done_at"] - r["at"]) / 60 for r in done[-DEV_TIMING_ROWS:])
    hour = [r for r in done if r["done_at"] > now - 3600]
    out = [r for r in rows if r.get("dev") and r.get("status") == "requested" and not r.get("done_at")
           and r["at"] > now - 6 * 3600]
    return {"done_last_hour": len(hour), "games_last_hour": sum(r.get("games", 0) for r in hour), "out": len(out),
            "minutes": {"n": len(minutes), "median": round(statistics.median(minutes), 1),
                        "slowest": round(minutes[-1], 1)} if minutes else {"n": 0}}


# The newest earlier submission with unranked games in online-replays outside `submission_id`'s play group
# (play_group: an identical build's games are this version's, not the one before), and those of its own group.
def previous_version(state: Path, submission_id: int) -> tuple[int | None, list[dict]]:
    folder = state / "online-replays"
    same = set(play_group(state, submission_id))
    earlier = sorted((int(p.name) for p in folder.iterdir() if p.name.isdigit() and int(p.name) < submission_id
                      and int(p.name) not in same), reverse=True) if folder.exists() else []
    for sid in earlier:
        games = [g for g in group_index(state, play_group(state, sid))["games"] if not g.get("ranked")]
        if games:
            return sid, games
    return None, []


# Same-code promotions (referee/samecode.py: the same wasm but for the build's sonar key) as {candidate commit: the
# champion commit its check built against, its evaluation's base}, from results.jsonl's kept rows with
# identity.same_code (each experiment's latest such row). The file is 270 MB (29 Sep), so only lines naming same_code
# are parsed (0.2 s), and the answer is kept while the file's size and mtime hold.
_same_code: tuple = (None, {})


def same_code_parents(state: Path) -> dict[str, str]:
    global _same_code
    path = state / "results.jsonl"
    try:
        info = path.stat()
    except OSError:
        return {}
    key = (str(path), info.st_size, info.st_mtime_ns)
    if _same_code[0] != key:
        rows = {}
        with path.open("rb") as handle:
            for line in handle:
                if b'"same_code": true' in line:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(row, dict):
                        rows[row.get("id")] = row
        parents = {}
        for row in rows.values():
            base = (row.get("evaluation") or {}).get("base")  # the champion its check built against, not the claim's
            if row.get("verdict") == "kept" and (row.get("identity") or {}).get("same_code") and row.get("candidate") \
                    and base:
                parents[row["candidate"]] = base
        _same_code = (key, parents)
    return _same_code[1]


# The submissions of ours that play exactly as `sid` does, `sid` first, then back in time: one built from a champion
# kept on the same-code gate plays as the one built from the champion it was checked against (same_code_parents),
# chained, and a commit uploaded twice as both (E0350's v0070, submission 11376, plays as v0069's 11349; E0348's v0069
# changed play, so 11349's group is itself). Their online games are one sample: every reader of our active
# submission's games or of a per-version sample (compare, map records, the audit's and coders' games, dev records,
# probes and counts, the sweep's pairs) reads the group as one version, which CLAUDE.md's "our exact active
# submission" means; publishing and upload checks keep the submission id. A submission's commit is uploads.jsonl's
# (upload_commit), or publishing.json's verified record. [sid] when no commit or promotion links it.
def play_group(state: Path, sid: int) -> list[int]:
    rows = upload_rows(state)
    commits = {s: c for s in {r.get("previous") for r in rows} if isinstance(s, int) and (c := upload_commit(rows, s))}
    verified = read(state / "publishing.json", {}).get("verified") or {}
    if isinstance(verified.get("submission_id"), int) and verified.get("commit"):
        commits[verified["submission_id"]] = verified["commit"]
    parents, group, commit, seen = same_code_parents(state), [sid], commits.get(sid), set()
    while commit and commit not in seen:
        seen.add(commit)
        group += sorted((s for s, c in commits.items() if c == commit and s not in group), reverse=True)
        commit = parents.get(commit)
    return group


# online.json's play group (play_group, written by each cycle with its submission_id), or its submission alone.
def same_play(report: dict) -> list[int]:
    return report.get("play_group") or [report.get("submission_id")]


# The indexes of the submissions in `group` (play_group's) as one: their saved games and their API results.
def group_index(state: Path, group: list[int]) -> dict:
    out: dict = {"games": [], "results": []}
    for sid in group:
        index = read(state / "online-replays" / str(sid) / "index.json", {})
        out["games"] += index.get("games", [])
        out["results"] += index.get("results", [])
    return out


# Our points in one game (1 a win, 0.5 a draw, 0 a loss): a saved game's replay summary names the winner, and an API
# result (an index's "results", collect_games) carries the site's, as "A", "B" or None.
def game_score(game: dict) -> float:
    winner = game["summary"]["result"]["winner"] if "summary" in game else game["winner"]
    return 0.5 if winner is None else float(winner == game["side"])


# How to read a version's index (online-replays/<version>/index.json) without mistaking a result-chosen set for a
# sample (MISSES 55). Its "games" are the saved replays: the result-blind sample and, marked "kept" ("loss": the
# DEV_LOSS_REPLAYS losses and draws kept because we lost), replays chosen by their result; its "results" are the other
# games, API results only. Rates, records and per-map or per-opponent comparisons count every game (all_games); a
# rate or mean needing the replays' summaries reads the unmarked ones (sample_games); the marked ones are for
# watching. Audit 1790492423260536000 counted "games" alone and read version 8213 losing 107 of 178 dev games (60%)
# when it lost 177 of 750 (24%, as before) and the sample 23 of 94.
def selected(game: dict) -> bool:
    return bool(game.get("kept"))


# Whether a saved game's replay FILE is left out of a result-blind sample of files: chosen by its result (selected), or
# a dev game of a version whose dev wins' files prune packed away ("dev_wins_pruned", prune.mark_dev_rows), where the files
# left are losses and draws though every row's summary is still the sample. A rate or mean read from replay files
# skips these; one from summaries uses sample_games.
def file_selected(game: dict) -> bool:
    return selected(game) or bool(game.get("dev_wins_pruned"))


# Every game an index records, each once: its API results and its saved games (the saved row wins).
def all_games(index: dict) -> list[dict]:
    games = {g["id"]: g for g in index.get("results", [])}
    games.update((g["id"], g) for g in index.get("games", []))
    return list(games.values())


# The saved games chosen blind to their result (an index's "games", or online.json's), those `selected` left out.
def sample_games(games: list[dict]) -> list[dict]:
    return [g for g in games if not selected(g)]


# The organisers' map pool change (rtrgrd, #announcements, 2026-09-27 01:18 UTC): from 21:00 AEST (11:00 UTC) a map
# can keep its id while its pearl timings change completely, so a game from before and one from after on "the same
# map" are on different maps. Every before/after pairing and per-map tally here keys on pool_period beside the map id.
# The time was announced "subject to delays": if the change lands later, move this to when it did (the organisers'
# next post, or the first replay whose pearl countdowns differ from the map's old ones), and turn it into a list of
# cutoffs if the pool changes again. Replays of games requested from 11:08 UTC give every TILE line as `x y 0 0`, and
# from about 12:40 UTC carry no pearlCountdown events either (none in 8636's and 8942's 466 games; bots still see
# countdowns).
POOL_CUTOFF = "2026-09-27T11:00:00Z"


# requested_at, the battle's requestedAt, is the only time a saved game carries, and a battle requested just before the
# cutoff may play after it: dev battles took a median 2.2 min from request to done, 4.0 at most, at ~1620 games an
# hour (the orchestrator's review, 2026-09-27), so ~50 would straddle it. Games requested this long before the cutoff
# pair with nothing (pairable).
POOL_STRADDLE = 10 * 60


# The pool period a game was requested in: "before <cutoff>", "straddling <cutoff>" (within POOL_STRADDLE before
# it), or "from <cutoff>"; "unknown" when it has no readable request time (it then pairs only with another such game).
def pool_period(game: dict) -> str:
    try:
        at = datetime.fromisoformat(str(game.get("requested_at")).replace("Z", "+00:00"))
    except ValueError:  # missing or unreadable
        return "unknown"
    if at.tzinfo is None:  # a time without a zone could be either side
        return "unknown"
    at, cutoff = at.timestamp(), datetime.fromisoformat(POOL_CUTOFF.replace("Z", "+00:00")).timestamp()
    return f"{'from' if at >= cutoff else 'straddling' if at >= cutoff - POOL_STRADDLE else 'before'} {POOL_CUTOFF}"


# The pool period of a game requested at `now` (epoch seconds).
def period_at(now: float) -> str:
    return pool_period({"requested_at": datetime.fromtimestamp(now, UTC).isoformat()})


# Whether a game can be paired with another across versions: not one requested within POOL_STRADDLE before the cutoff,
# whose map may have had either pearl timings when it played.
def pairable(game: dict) -> bool:
    return not pool_period(game).startswith("straddling ")


# From the game requested at this time the site plays every map in two layouts, picked per game: the file's and one
# with the DRAGON lines' team ids swapped (same bodies, same file order, so the same turn order), and Prisoners
# Dilemma with and without 4 extra length-2 dragons, so in four (MISSES 72). A game's side no longer says where we
# start: 9553's alert against 9347 (28 ladder pairs by side, 7 worse and 1 better, p 0.035) is 19 pairs by start, 4
# worse and 1 better (p 0.19). In 700 saved replays of 9347-9762 every map has one start per team and dragon set
# before it and two from it.
LAYOUT_CUTOFF = "2026-09-28T02:00:00Z"


# The layout and our start of a saved replay for its index row: "layout", its map's layout with the teams' labels
# (ladder_maps.layout_hash, 16 hex digits), which tells a map's layouts apart (layout_news), and "start", where our
# `side` started (ladder_maps.start_hash), which start_key pairs on. {} when the replay can't be read (a bad replay
# is caught by its summary; refresh_starts tries again).
def start_fields(path: Path, side: str) -> dict:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import replays

    try:
        text = replays.load_replay(path).map
    except Exception:
        return {}
    return {"layout": ladder_maps.layout_hash(text)[:16], "start": ladder_maps.start_hash(text, side)}


# Where a game started us, for pairing across versions and per-map rows: its "start" (start_fields) once its replay
# was read; else "side A" or "side B" for a game requested before LAYOUT_CUTOFF less POOL_STRADDLE, or at no readable
# time, when each map had one layout (but for Prisoners Dilemma's two dragon sets, 19:38 UTC 27 Sep on); else None, a
# later game with no replay read (a dev result, a pruned replay): unpairable, counted as such in compare_versions. A
# side key never equals a start, so a game without a replay pairs only with another such game.
def start_key(game: dict) -> str | None:
    if game.get("start"):
        return game["start"]
    try:
        at = datetime.fromisoformat(str(game.get("requested_at")).replace("Z", "+00:00")).timestamp()
    except ValueError:
        at = None
    cutoff = datetime.fromisoformat(LAYOUT_CUTOFF.replace("Z", "+00:00")).timestamp() - POOL_STRADDLE
    return f"side {game.get('side')}" if at is None or at < cutoff else None


# Adds start_fields to up to `limit` saved games of these submissions that have none and whose replay is still here
# (games saved before the fields existed, or whose replay couldn't be read then), about 10 ms each; one whose replay
# can't be read is marked "start_failed" and left.
def refresh_starts(state: Path, submissions: list[int], limit: int = 1000) -> None:
    for submission in submissions:
        path = state / "online-replays" / str(submission) / "index.json"
        saved = read(path, None)
        if not saved:
            continue
        changed = False
        for game in saved["games"]:
            if limit <= 0:
                break
            if game.get("start") or game.get("start_failed") or not game.get("side") \
                    or not Path(game.get("replay") or "").is_file():
                continue
            limit -= 1
            changed = True
            game.update(start_fields(Path(game["replay"]), game["side"]) or {"start_failed": True})
        if changed:
            save(path, saved)


# Alert lines for map layouts not seen before in our saved games (`games`, rows with start_fields' "layout"), and the
# updated record `known` ({map id: [layouts]}, online.json's "layouts"). A new layout changes what a key read from a
# map means (MISSES 72: the swapped start tiles broke compare's side key for a day, unnoticed), so each is news:
# check every reader that keys on a map, side or start. The first call that sees layouts (no record yet) gives one line
# for the lot; the record stays None until then.
def layout_news(known: dict | None, games: list[dict]) -> tuple[dict, list[str]]:
    record = {str(k): list(v) for k, v in (known or {}).items()}
    fresh = defaultdict(set)
    for game in games:
        if (layout := game.get("layout")) and layout not in record.get(str(game.get("map_id")), []):
            fresh[str(game.get("map_id"))].add(layout)
    for map_id, layouts in fresh.items():
        record[map_id] = sorted(set(record.get(map_id, [])) | layouts)
    if not fresh:
        return (None if known is None else record), []
    if known is None:
        several = sorted((m for m, v in record.items() if len(v) > 1), key=lambda m: int(m) if m.isdigit() else 0)
        return record, [f"online: map layouts now recorded for {len(record)} maps ({sum(map(len, record.values()))} "
                        f"layouts; more than one on maps {', '.join(several) or 'none'}); a new one alerts (MISSES 72)"]
    return record, [f"online: map {m} is played in a layout not seen before ({', '.join(sorted(v))}; {len(record[m])} "
                    "now): check every reader keyed on the map, side or start still means what it did (MISSES 72)"
                    for m, v in sorted(fresh.items())]


# Before/after pairs: this version's games against the same opponent version, map, start (start_key: where on the map
# we started, whichever side's label we had) and pool period (pool_period) as the previous version's. Since 2026-09-25
# 17:00 every match draws its own pearl seed, so a pair is the same matchup under different pearl luck, not an exact
# replay of it: read the counts as a noisy paired sample. "unpairable" counts each version's games start_key can't
# place, and "unknown_opponent" those whose opponent version is unknown (None: the site stopped naming it, API_GONE).
# Those pair by the rating rule (method M73, with `splits`, rating_splits' record): the latest such game of the
# previous version and the earliest of this one on each team, map, start and period count as one opponent version
# when no rating read (every RATINGS_EVERY) splits them (unsplit), and join the named pairs in every statistic, so in
# hold_replays and version_alert, which with them only ever asks for a confirming sample; raise_rechecks reads the
# named pairs alone ("named", named_view). "inferred" counts them, "candidates" the matchups that can pair (a named
# pair, or an unknown version on either side with a previous game), paired or not, and "unread" those of an unknown
# version no rating read covers yet (the online-compare health check waits for those). The rule sees only a new
# bot's K 96 (docs/unsw/elo.md: a second new bot within 12 h starts at a count up to 10, K 24, and shows nothing, and
# an older bot activated again keeps its own count), so it leaves most of the other bots in: of the 322 pairs by
# team, map, start and period between consecutive versions whose opponents the site named inside
# loop/testdata/ratings-sample.json's history (ranks 1-37, 2026-09-26 to 09-28), 138 (43%) met another of the
# opponent's submissions, and of the 274 the rule keeps, 104 (38%); such pairs read -0.005 a pair (83 better, 86
# worse) against +0.023 (89, 70) for the same bot (1,388 named pairs to 2026-09-28), so they dilute a change more than
# they fake one.
def compare_versions(previous_sid: int, previous: list[dict], current: list[dict], splits: dict | None = None) -> dict:
    named = lambda g: g.get("opponent_submission_id") is not None
    return {"previous": previous_sid, **paired_games(previous, current, splits),
            "unpairable": {"before": sum(pairable(g) and start_key(g) is None for g in previous),
                           "after": sum(pairable(g) and start_key(g) is None for g in current)},
            "unknown_opponent": {"before": sum(not named(g) for g in previous),
                                 "after": sum(not named(g) for g in current)}}


# compare_versions' pair statistics: named games paired on opponent team, opponent version, map, start and pool
# period; with `splits`, games of an unknown opponent version on team, map, start and period when unsplit. With any
# such inferred pair, "named" holds the statistics of the named pairs alone (named_view).
def paired_games(previous: list[dict], current: list[dict], splits: dict | None = None) -> dict:
    named = lambda g: g.get("opponent_submission_id") is not None
    loose = lambda g: (g["opponent"], g["map_id"], start_key(g), pool_period(g))
    key = lambda g: (*loose(g), g.get("opponent_submission_id"))
    usable = lambda g: pairable(g) and start_key(g) is not None
    before = {key(g): g for g in previous if usable(g) and named(g)}
    pairs = {key(g): (before[key(g)], g) for g in current if usable(g) and named(g) and key(g) in before}
    at = lambda g: str(g.get("requested_at") or "")
    last = {}  # the latest previous game and the earliest current one of each matchup, one version unknown
    for g in sorted((g for g in previous if usable(g)), key=at):
        last[loose(g)] = g
    first = {}
    for g in sorted((g for g in current if usable(g)), key=at):
        if not (named(g) and named(last.get(loose(g), g))):
            first.setdefault(loose(g), g)
    # Matchups that can pair (a named pair, or an unknown version on either side), as "unread" counts them.
    candidates = len({k[:4] for k in pairs} | {k for k in first if k in last})
    inferred = {}
    waiting = 0
    for k, g in first.items():
        if k not in last or splits is None:
            continue
        if (same := unsplit(splits, k[0], at(last[k]), at(g))) is None:
            waiting += 1
        elif same:
            inferred[("inferred", *k)] = (last[k], g)
    out = {"inferred": len(inferred), "candidates": candidates, "unread": waiting, **pair_stats({**pairs, **inferred})}
    if inferred:
        out["named"] = {k: v for k, v in pair_stats(pairs).items() if k in NAMED_STATS}
    return out


# The pair statistics compare records: {pair key: (previous version's game, this version's)}.
def pair_stats(pairs: dict[tuple, tuple[dict, dict]]) -> dict:
    diffs = {k: game_score(a) - game_score(b) for k, (b, a) in pairs.items()}
    n = len(diffs)
    opponents = Counter((a["opponent"], a.get("opponent_submission_id")) for _, a in pairs.values())
    facts = lambda read: margin_change({k: (read(b), read(a)) for k, (b, a) in pairs.items()})
    return {"pairs": n,
            "opponents": [{"opponent": o, "submission": s, "pairs": c} for (o, s), c in opponents.most_common()],
            "before": round(sum(game_score(b) for b, _ in pairs.values()) / n, 3) if n else None,
            "after": round(sum(game_score(a) for _, a in pairs.values()) / n, 3) if n else None,
            "better": sum(d > 0 for d in diffs.values()), "worse": sum(d < 0 for d in diffs.values()),
            "same": sum(d == 0 for d in diffs.values()),
            "worse_games": [pairs[k][1]["id"] for k, d in diffs.items() if d < 0][:20],
            "longest_margin": facts(longest_margin),
            "ours": {fact: facts(lambda g: our_facts(g)[fact]) for fact in OUR_FACTS},
            "concentration": {who: {fact: facts(lambda g: concentration(g, who)[fact]) for fact in CONCENTRATION}
                              for who in ("ours", "theirs")}}


# What the named pairs alone say, for the readers M73's inferred pairs must not reach: raise_rechecks (sig,
# 2026-09-29: re-checks only on measured evidence of harm; the rating rule can't see a team swapping in an old bot in
# unranked games, and an older submission activated again carries on from its own count, K 24 once it has played 10:
# docs/unsw/elo.md) and version_alert's "find out why".
NAMED_STATS = ("pairs", "opponents", "before", "after", "better", "worse", "same", "longest_margin")


def named_view(compare: dict | None) -> dict:
    compare = compare or {}
    return {**compare, **compare["named"], "inferred": 0} if "named" in compare else compare


# The rating reads M73's rule pairs by: GET /ratings (every team's rating history, a point per settled ranked battle
# and the read's own at the current rating, risers.py's docstring) at most every RATINGS_EVERY by the online cycle
# (refresh_splits), kept as <loop home>/rating-splits.json {"read_at", "attempt_at", "error", "teams": {team id:
# {"from", "to", "splits": [[t0, t1], ...]}}}: the span the reads covered and each step larger than SETTLED_K between
# two points (epoch seconds), which only a new bot's K gives (a bot's first ranked battles, K 96 falling to 24 by its
# tenth: docs/unsw/elo.md), kept SPLITS_KEEP_S. One team's entry is a few splits a day at most (ranks 1-37 over 49 h:
# 0-7 each, loop/testdata/ratings-sample.json), so the file stays under 1 MB for the ladder's 1,000 teams.
RATINGS_EVERY = 600  # so a game waits at most about this long for the read that judges it (one read a cycle at most)
SETTLED_K = 24
SPLITS_KEEP_S = 7 * 86400


def moment(text: object) -> float | None:
    try:
        at = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return at.timestamp() if at.tzinfo else None


# `old` (rating_splits' record) merged with one GET /ratings payload's histories, read at `now`. Splits older than
# SPLITS_KEEP_S go, and each team's `from` moves up to that horizon with them, so no game before it counts as covered
# (a pruned split would otherwise read as none); a team whose reads all ended before it goes.
def rating_splits(payload: object, old: dict | None, now: float) -> dict:
    teams = {k: dict(v) for k, v in ((old or {}).get("teams") or {}).items() if isinstance(v, dict)}
    histories = payload.get("histories") if isinstance(payload, dict) else None
    for team, history in (histories or {}).items():
        points = sorted((t, float(p["elo"])) for p in history if isinstance(p, dict)
                        and isinstance(p.get("elo"), (int, float)) and (t := moment(p.get("date"))) is not None)
        if not points:
            continue
        entry = teams.get(str(team)) or {}
        splits = {tuple(s) for s in entry.get("splits", [])}
        splits |= {(t0, t1) for (t0, e0), (t1, e1) in zip(points, points[1:]) if abs(e1 - e0) > SETTLED_K}
        teams[str(team)] = {"from": min(points[0][0], entry.get("from", math.inf)),
                            "to": max(points[-1][0], entry.get("to", 0)), "splits": sorted(map(list, splits))}
    horizon = now - SPLITS_KEEP_S
    teams = {k: {**v, "from": max(v.get("from", horizon), horizon),
                 "splits": [s for s in v.get("splits", []) if s[1] > horizon]}
             for k, v in teams.items() if v.get("to", 0) > horizon}
    return {"read_at": now, "error": None, "teams": teams}


# Whether no rating read splits team `team`'s games requested at `a` and `b` (ISO times, a first): no step larger than
# SETTLED_K lies between them (a split's span overlaps theirs: the new bot began before its first big step and after
# the point before it) in reads covering both. None when the reads don't cover them yet (b after the last read: the
# next one may split them), False when they never will (a before the team's first point, no team entry, a bad time).
def unsplit(splits: dict, team: int, a: str, b: str) -> bool | None:
    entry = (splits.get("teams") or {}).get(str(team))
    ta, tb = moment(a), moment(b)
    if not entry or ta is None or tb is None or not entry["from"] <= ta <= tb:
        return False
    if tb > entry["to"]:
        return None
    return not any(s0 < tb and s1 > ta for s0, s1 in entry["splits"])


# rating-splits.json, refreshed by one GET /ratings when the last good read is RATINGS_EVERY old; a failed read keeps
# the last good teams with its error and `attempt_at`, and is tried again REQUEST_EVERY later (the compare meanwhile
# pairs only what the last good read covers; health.online_compare names the error).
def refresh_splits(state: Path, now: float, fetch: Callable[[str], object] | None = None) -> dict:
    path = state / "rating-splits.json"
    old = read(path, {})
    if now - old.get("read_at", 0) < RATINGS_EVERY or now - old.get("attempt_at", 0) < REQUEST_EVERY:
        return old
    try:
        new = rating_splits((fetch or ladder.get)("/ratings"), old, now)
    except (Exception, SystemExit) as error:  # ladder.api_key exits without a key
        new = {**old, "attempt_at": now, "error": f"{type(error).__name__}: {error}"[:300]}
    save(path, new)
    return new


# compare_versions over ladder and autoscrim games only (against_dev, `dev` the dev team ids), or None without a
# previous version: dev games have their own records and compares (dev_report), and at a few hundred an hour their
# pairs, winning and losing by turns, would drown a ladder regression in version_alert's and margin_fell's sign tests
# (6 ladder pairs worse and 1 better alert at p 0.06; with 30 dev pairs better and 30 worse beside them, p 0.3).
# per_map is kept to ladder and autoscrim games in the same way.
def ladder_compare(previous_sid: int | None, previous: list[dict], current: list[dict], dev: set[int],
                   splits: dict | None = None) -> dict | None:
    if not previous_sid:
        return None
    return compare_versions(previous_sid, [g for g in previous if not against_dev(g, dev)],
                            [g for g in current if not against_dev(g, dev)], splits)


# Our saved games and API results against each team in `opponents`, per version of ours up to `submission_id`, each
# game once and oldest first: {team: {version: [games]}}. `games` are the current version's saved games, as
# collect_games returned them; every version's index adds all its games (all_games: saved games and API results),
# under its play group's newest member (play_group), so `submission_id`'s group reads as `submission_id`.
def played_against(state: Path, opponents: set[int], submission_id: int,
                   games: list[dict]) -> dict[int, dict[int, list[dict]]]:
    by_version: dict[int, dict] = defaultdict(dict)
    paths = {int(p.parent.name): p for p in (state / "online-replays").glob("*/index.json")
             if p.parent.name.isdigit() and int(p.parent.name) <= submission_id}
    head: dict[int, int] = {}  # each version's newest play-group member, `submission_id` for its own group
    for version in sorted({*paths, submission_id}, reverse=True):
        if version not in head:
            head.update((s, version) for s in play_group(state, version) if s not in head)
    for version, path in paths.items():
        by_version[head[version]].update((g["id"], g) for g in all_games(read(path, {})))
    by_version[submission_id].update((g["id"], g) for g in games)
    out: dict[int, dict[int, list[dict]]] = defaultdict(dict)
    for version, rows in by_version.items():
        for team in opponents:
            mine = sorted((g for g in rows.values() if g.get("opponent") == team),
                          key=lambda g: (g.get("requested_at") or "", g["id"]))
            if mine:
                out[team][version] = mine
    return out


# Our games by the side we were on, {"A": n, "B": n}, sides we never had left out (review M48: every dev battle has
# put us on side A, and the battle API can't choose, docs/unsw/api.md).
def side_counts(games: list[dict]) -> dict[str, int]:
    return dict(sorted(Counter(g.get("side") or "?" for g in games).items()))


# One version's games against one dev submission as a record: games, wins, draws and losses, and the score (a draw
# half a win) with its 95% interval (chance.rate_ci), so the range shows how many games the time allowed, and our
# games by side (side_counts).
def record(games: list[dict]) -> dict:
    scores = [game_score(g) for g in games]
    points = sum(scores)
    return {"games": len(games), "wins": scores.count(1.0), "draws": scores.count(0.5), "losses": scores.count(0.0),
            "score": round(points / len(games), 3), "ci": list(rate_ci(points, len(games))),
            "sides": side_counts(games)}


# Every version's records against one dev team (`played`, one team's part of played_against) from `first` on, one per
# dev submission it played and pool period (pool_period), oldest first: dev teams update their bot and from
# POOL_CUTOFF a map id may carry other pearl timings, so a record never pools two submissions or two periods. Games
# whose dev submission the site no longer names (None, API_GONE) make one record of their own after the rest: it may
# hold more than one dev bot.
def team_records(played: dict[int, list[dict]], first: int = 0) -> list[dict]:
    records = []
    for version in sorted(v for v in played if v >= first):
        by_dev = defaultdict(list)
        for game in played[version]:
            by_dev[game.get("opponent_submission_id"), pool_period(game)].append(game)
        order = lambda item: (item[0][0] is None, item[0][0] or 0,
                              min(str(g.get("requested_at") or "") for g in item[1]))
        for (dev_submission, pool), rows in sorted(by_dev.items(), key=order):
            records.append({"submission_id": version, "dev_submission_id": dev_submission, "pool": pool,
                            **record(rows)})
    return records


# Two versions' games against one dev submission, map by map: each map and pool period (pool_period) both played,
# with each version's score and games there and the change, and a sign test over the maps that changed
# (chance.sign_p, one-sided each way, with n), and both versions' games by our side (side_counts). A report, never a
# gate.
def map_compare(before: list[dict], after: list[dict]) -> dict:
    before, after = [g for g in before if pairable(g)], [g for g in after if pairable(g)]
    where = lambda g: (g["map_id"], pool_period(g))
    maps = sorted({where(g) for g in before} & {where(g) for g in after}, key=lambda m: (m[0] or 0, m[1]))
    rows, better, worse = [], 0, 0
    for map_id, pool in maps:
        b = [game_score(g) for g in before if where(g) == (map_id, pool)]
        a = [game_score(g) for g in after if where(g) == (map_id, pool)]
        change = sum(a) * len(b) - sum(b) * len(a)  # exact: every score is a multiple of a half
        better, worse = better + (change > 0), worse + (change < 0)
        rows.append({"map_id": map_id, "pool": pool, "before": round(sum(b) / len(b), 3), "before_games": len(b),
                     "after": round(sum(a) / len(a), 3), "after_games": len(a),
                     "change": round(sum(a) / len(a) - sum(b) / len(b), 3)})
    return {"maps": rows, "better": better, "worse": worse, "same": len(rows) - better - worse, "n": better + worse,
            "p_better": round(sign_p(better, worse), 3), "p_worse": round(sign_p(worse, better), 3),
            "sides": side_counts(before + after)}


# This version against the latest earlier one that played the same dev team (`played`, as in team_records), only ever
# on one dev submission both played: a map_compare per shared dev submission, or none and a note saying so when they
# share none. None when either has no games against it.
def dev_compare(played: dict[int, list[dict]], submission_id: int) -> dict | None:
    earlier = [v for v in played if v < submission_id]
    if not played.get(submission_id) or not earlier:
        return None
    previous = max(earlier)
    subs = lambda v: sorted({g.get("opponent_submission_id") for g in played[v]}, key=lambda s: s or 0)
    shared = [s for s in subs(submission_id) if s is not None and s in subs(previous)]  # None: unknown, API_GONE
    out: dict = {"previous": previous, "shared": shared,
                 "by_dev_submission": [{"dev_submission_id": s, **map_compare(
                     [g for g in played[previous] if g.get("opponent_submission_id") == s],
                     [g for g in played[submission_id] if g.get("opponent_submission_id") == s])} for s in shared]}
    if not shared:
        out["note"] = (f"no dev submission in common ({submission_id} played {subs(submission_id)}, {previous} played "
                       f"{subs(previous)}), so not compared")
    return out


LOCAL_SIDE_RUNS = official.RECENT  # the newest official-map reports local_sides looks through for our commit


# Our uploaded bot's local score on each ladder map as each side, for online.md's dev section (review M48): every dev
# battle has put us on side A (the battle API can't choose, docs/unsw/api.md; on 2026-09-27, 340 of 340 of 8023's games
# against the benchmark, and every dev game of every version and dev team saved then), so a dev map row mixes the map
# with the side, and this tells them apart: in E0167 both bots scored 0.6 as side A on Trophy, Devil and Trauma, where
# the benchmark held 8023 to 0.18-0.21, so those deficits weren't the side's. From the newest official-map report (runs/<id>/official.jsonl,
# driver.check_official; among the LOCAL_SIDE_RUNS highest-numbered runs with an official.json) in which submission
# `sid`'s verified commit (publishing.json) played, as the candidate or as its base: the two bots are one change apart,
# so a side's score is near self-play's, under 0.5 where that side itself loses on that map. Maps get the site's ids
# by content (ladder-maps.json against the report's copy 0 of each map). Returns {commit, experiment, role, opponent,
# maps: [{map_id, map, A: {score, games}, B: ...}]}, or {note} saying why there is none. Reads one run's
# evaluation.json (about 200 KB) a cycle when the newest report is ours.
def local_sides(state: Path, sid: int) -> dict:
    verified = official.read_json(state / "publishing.json").get("verified") or {}
    commit = verified.get("commit")
    if verified.get("submission_id") != sid or not commit:
        return {"note": f"submission {sid}'s commit isn't verified in publishing.json"}
    ids = {m.get("content"): m.get("id") for m in ladder_maps.read(state).get("maps") or []}
    runs = sorted((p.parent for p in (state / "runs").glob("E*/official.json") if p.parent.name[1:].isdigit()),
                  key=lambda run: int(run.name[1:]), reverse=True)
    for run in runs[:LOCAL_SIDE_RUNS]:
        policy = official.read_json(run / "evaluation.json")
        ours = "a" if policy.get("candidate") == commit else "b" if policy.get("base") == commit else None
        if not ours or not (run / "official.jsonl").exists():
            continue
        per_map: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(lambda: [0.0, 0]))
        for row in official.json_rows((run / "official.jsonl").read_text().splitlines()):
            points = {"a": 1.0, "b": 0.0, "draw": 0.5}.get(row.get("winner"))
            if points is None or row.get("a_side") not in ("A", "B"):
                continue
            side = row["a_side"] if ours == "a" else "B" if row["a_side"] == "A" else "A"
            tally = per_map[Path(row["map"]).stem.split("~")[0]][side]
            tally[0] += points if ours == "a" else 1 - points
            tally[1] += 1
        if not per_map:  # no game there names its side: look further back
            continue
        maps = []
        for stem, sides in sorted(per_map.items()):
            copy = run / "official-maps" / f"{stem}~0.map"
            scores = {side: {"score": round(p / n, 3), "games": n} for side, (p, n) in sorted(sides.items())}
            maps.append({"map_id": ids.get(ladder_maps.content_hash(copy.read_text())) if copy.exists() else None,
                         "map": stem, **scores})
        return {"commit": commit, "experiment": run.name, "role": "candidate" if ours == "a" else "base",
                "opponent": policy.get("base" if ours == "a" else "candidate"), "maps": maps}
    return {"note": f"no official-map report among the newest {LOCAL_SIDE_RUNS} played {commit[:7]}"}


# Our record against the dev benchmark per version of ours and per dev submission, oldest first: the strongest dev bot,
# so a strength curve across our versions, and `compare` (dev_compare) against the version before on the same dev
# submission. benchmark_news reads the records' dev submissions. `played` is played_against's, when the caller has it.
def benchmark_report(state: Path, bench: dict, submission_id: int, games: list[dict],
                     played: dict[int, dict[int, list[dict]]] | None = None) -> dict:
    mine = (played if played is not None else played_against(state, {bench["id"]}, submission_id, games)).get(
        bench["id"], {})
    return {"team": bench, "records": team_records(mine), "compare": dev_compare(mine, submission_id)}


# Every dev team we can play (dev_opponents), each on its own, for online.json's `dev` and online.md: its share of our
# dev battles (dev_weight) with the score and 95% lower bound it comes from (`score`, `lower`: audit.dev_picks reads
# them), from our latest DEV_WEIGHT_GAMES games against its current submission, and this version's and the compared
# version's records per dev submission (team_records) with the compare (dev_compare). `played` as in benchmark_report.
# The share pools every version of ours and both pool periods (the records never do): it only orders who plays next,
# and kept to the latest pool period (194b9cd, 2026-09-27) it restarted at the 21:00 pool change, leaving the teams we
# always beat on 20-30 games there (0.12-0.17 each) where they had 290-340 wins of as many. While the latest game
# names no dev submission (API_GONE) the share reads the team's latest games whatever submission they name: counting
# only the unnamed ones would restart every team's share at 1, spreading battles evenly for about two days (MISSES 77).
def dev_report(state: Path, dev: list[dict], submission_id: int, games: list[dict],
               played: dict[int, dict[int, list[dict]]] | None = None) -> list[dict]:
    if played is None:
        played = played_against(state, {t["id"] for t in dev}, submission_id, games)
    out = []
    for team in sorted(dev, key=lambda t: (-(t.get("elo") or 0), t["id"])):
        mine = played.get(team["id"], {})
        latest = [g for v in sorted(mine) for g in mine[v]]
        latest.sort(key=lambda g: (g.get("requested_at") or "", g["id"]))
        current = latest[-1].get("opponent_submission_id") if latest else None
        now = lambda g: current is None or g.get("opponent_submission_id") == current
        scores = [game_score(g) for g in latest if now(g)][-DEV_WEIGHT_GAMES:]
        compare = dev_compare(mine, submission_id)
        out.append({"id": team["id"], "name": team.get("name"), "elo": team.get("elo"),
                    "weight": round(dev_weight(scores), 3), "weight_games": len(scores),
                    "score": round(sum(scores) / len(scores), 3) if scores else None,
                    "lower": rate_ci(sum(scores), len(scores))[0] if scores else None, "dev_submission_id": current,
                    "games": sum(map(now, mine.get(submission_id, []))),
                    "records": team_records(mine, compare["previous"] if compare else submission_id),
                    "compare": compare})
    return out


# Lines for host-alerts.log: which team is the benchmark when that changes, and each new dev submission it plays (a
# new frontier version is news in itself, and its games start a new, separately paired record).
def benchmark_news(old: dict | None, new: dict) -> list[str]:
    team = new["team"]
    label = f"team {team['id']} ({team['name']}, {team['elo']})"
    if not old or old.get("team", {}).get("id") != team["id"]:
        return [f"online: the dev benchmark is {label}, {team['why']}"]
    seen = sorted({r["dev_submission_id"] for r in old.get("records", [])}, key=lambda s: s or 0)
    # A record of unnamed submissions (None, API_GONE) is no new version.
    fresh = sorted({r["dev_submission_id"] for r in new["records"]} - set(seen) - {None})
    return [f"online: the dev benchmark {label} plays a new submission {s} (before: {seen}), a new version of the "
            "organisers' bot; our games against it are recorded and paired separately" for s in fresh] if seen else []


WATCHED_TOP = 10  # the ladder's top non-dev teams whose version changes alert, beside every dev team and the benchmark


# Each watched team's live submission, as its latest game in `played` (played_against's, over every version of ours)
# shows it: {team id as a string: submission id}. A team's version is known only once we play it. Since API_GONE no
# new game names one, so this keeps each team's last named version and version_news stays quiet (MISSES 77).
def opponent_versions(played: dict[int, dict[int, list[dict]]]) -> dict[str, int]:
    out = {}
    for team, versions in played.items():
        latest = max((g for rows in versions.values() for g in rows if g.get("opponent_submission_id")),
                     key=lambda g: (g.get("requested_at") or "", g["id"]), default=None)
        if latest:
            out[str(team)] = latest["opponent_submission_id"]
    return out


# Alert lines for each watched team whose live submission changed between `old` and `new` (opponent_versions), named
# from `teams` (the leaderboard); cycle leaves the benchmark to benchmark_news. REFLECT 2026-09-28 14:51 F4: dev test 1 moved from 4525 to 9371 and nothing said
# so; its old version's 718 replays were never studied (health.unstudied_versions lists such versions).
def version_news(old: dict[str, int], new: dict[str, int], teams: list[dict]) -> list[str]:
    names = {str(t["id"]): t.get("name") for t in teams}
    return [f"online: team {team} ({names.get(team)}) plays a new submission {sid} (before: {old[team]}); "
            "its old version's saved games are listed by health until studied"
            for team, sid in sorted(new.items()) if team in old and old[team] != sid]


# Our longest dragon minus theirs when the game ended, or None without both: an endgame change (a fold, growth)
# can move this without flipping any winner (review M10: E0059 flipped none of 17 matchups).
def longest_margin(game: dict) -> int | None:
    teams = game["summary"].get("teams", {})
    ours = teams.get(game["side"], {}).get("end", {}).get("longest")
    theirs = teams.get("B" if game["side"] == "A" else "A", {}).get("end", {}).get("longest")
    return None if ours is None or theirs is None else ours - theirs


OUR_FACTS = ("second", "total", "few_pct", "alive100", "pearls100")


# Our side of one game, for `compare` (reviews M15 and M9): where the rest of our length was at the round limit
# (second-longest dragon, total length, 100 when we had 8 or fewer dragons else 0; None after an elimination, where
# few dragons means losses, not a fold, as in driver.round500) and how the opening went (dragons alive at round 100,
# pearls eaten in rounds 0-99). None where the summary lacks it: `second` is only in summaries from 2026-09-26.
def our_facts(game: dict) -> dict[str, float | None]:
    team = game["summary"].get("teams", {}).get(game["side"], {})
    full = game["summary"].get("result", {}).get("endReason") == "roundLimit"
    end = team.get("end", {}) if full else {}
    early = team["behaviour"].get("pearlsByPhase", {}).get("early", 0) if "behaviour" in team else None
    return {"second": end.get("second"), "total": end.get("total"),
            "few_pct": None if end.get("alive") is None else 100.0 * (end["alive"] <= 8),
            "alive100": team.get("aliveAt100"), "pearls100": early}


CONCENTRATION = ("longest400", "keep_pct")


# One side's ("ours" or "theirs") length before the endgame and how much of it ended in one dragon, for `compare`
# (review M21: in 21 of 41 round-limit losses our total at r400 was at least theirs, but our longest there was median
# 6 to their 18, and our r499 longest was 0.22 of our r400 total to their 0.32): the longest dragon at round 400,
# and 100 x the longest at the round limit over the total at round 400. None where the summary predates `at400`
# (2026-09-26) or the game ended before round 400, and keep_pct also when it ended before the round limit.
def concentration(game: dict, who: str) -> dict[str, float | None]:
    side = game["side"] if who == "ours" else "B" if game["side"] == "A" else "A"
    team = game["summary"].get("teams", {}).get(side, {})
    at400 = team.get("at400")
    longest = team.get("end", {}).get("longest")
    full = game["summary"].get("result", {}).get("endReason") == "roundLimit"
    return {"longest400": at400["longest"] if at400 else None,
            "keep_pct": 100.0 * longest / at400["total"] if full and at400 and at400["total"] and longest is not None
            else None}


def margin_change(pairs: dict) -> dict:
    known = [(b, a) for b, a in pairs.values() if b is not None and a is not None]
    if not known:
        return {"pairs": 0}
    return {"pairs": len(known), "before": round(sum(b for b, _ in known) / len(known), 1),
            "after": round(sum(a for _, a in known) / len(known), 1),
            "up": sum(a > b for b, a in known), "down": sum(a < b for b, a in known)}


# A warning for the orchestrator when the live version does clearly worse than the previous one in the same
# matchups (at least 18 pairs, 10 points lower), or None. Local tests can miss a style; see CLAUDE.md.


# "Clearly worse" means the changed pairs lean worse beyond luck (sign test p <= 0.1; 6 worse and 1 better is 0.06),
# not only a 0.1 drop: with fresh pearl seeds per match, 3 worse and 1 better of 18 pairs (7003 vs 6925, 2026-09-26)
# happens by luck 31% of the time and alerted anyway.
def clearly_worse(compare: dict | None) -> bool:
    compare = compare or {}
    return not (compare.get("pairs", 0) < 18 or compare["after"] - compare["before"] > -0.1
                or sign_p(compare.get("worse", 0), compare.get("better", 0)) > TRIGGER_P)


# The alert also counts the versions checked so far (each upload that replaced a live one, uploads.jsonl): at
# TRIGGER_P a check, luck alone gives up to that many times TRIGGER_P alerts (REFLECT 2026-09-28 17:52 F7: v0051's p
# 0.035 of about 50 checks, and a mechanism search began before the re-paired count read p 0.19). Only a p at most
# TRIGGER_P over that count (a Bonferroni bar) sends the orchestrator straight to why local tests missed it; any other
# first waits for a confirming sample: this version's next games on the same matchups, or its re-checks. M73's
# inferred pairs count toward the alert but never toward the bar (named_view): with them the alert always waits. The
# text gives the comparison's own record and p, alone and corrected over the versions checked (REFLECT 29 Sep 17:32 F1).
def version_alert(state: Path) -> str | None:
    report = read(state / "online.json", {})
    compare = report.get("compare") or {}
    if not clearly_worse(compare):
        return None
    checks = versions_checked(state)
    p = sign_p(compare["worse"], compare["better"])
    named = named_view(compare)
    inferred = compare.get("inferred", 0)
    caveat = (f"; {inferred} of the {compare['pairs']} pairs are inferred (M73: an opponent version the site no longer "
              "names, paired when no rating read shows it a new bot; about 38% of such pairs meet another of the "
              "opponent's bots, and a team swapping in an older bot shows no rating step), so this alert never skips "
              "the confirming sample" if inferred else "")
    if clearly_worse(named) and sign_p(named["worse"], named["better"]) <= TRIGGER_P / checks:
        next_step = "find out why local tests missed it (CLAUDE.md, Question the tests) and tell sig"
    else:
        bar = (f"p is under the {TRIGGER_P:g} bar but not under {TRIGGER_P / checks:.3f} ({TRIGGER_P:g} over those "
               "checks)" if not inferred else f"the named pairs alone don't pass the {TRIGGER_P / checks:.3f} bar")
        next_step = (f"{bar}, so wait for a confirming sample (this version's next games on the same matchups, or its "
                     "re-checks) before looking for why local tests missed it; tell sig it may be luck")
    return (f"online: submission {report['submission_id']} scored {compare['after']:.2f} against "
            f"{compare['before']:.2f} for submission {compare['previous']} on {compare['pairs']} same matchups "
            f"({compare['worse']} worse, {compare['better']} better, sign test p {p:.3f}{caveat}); one of {checks} "
            f"versions checked, where luck alone gives up to {checks * TRIGGER_P:.1f} such alerts (p {p:.3f} alone, "
            f"{min(1.0, p * checks):.3f} over the {checks}); {next_step}")


# uploads.jsonl's rows under `state` ([] with no file). play_group reads it every online cycle, map-records check and
# audit, so a corrupt line anywhere is skipped and alerted (once per line number and process: _bad_uploads), never
# raised; a half-written last line is skipped silently, as official.json_rows does.
_bad_uploads: set[tuple[str, int]] = set()


def upload_rows(state: Path) -> list[dict]:
    log = state / "uploads.jsonl"
    if not log.exists():
        return []
    bad: list[int] = []
    rows = official.json_rows(log.read_text().splitlines(), bad)
    if new := [n for n in bad if (str(log), n) not in _bad_uploads]:
        _bad_uploads.update((str(log), n) for n in new)
        meta.alert(state, f"online: uploads.jsonl line(s) {', '.join(map(str, new))} aren't JSON; skipped by every "
                          "reader (online.upload_rows) until repaired")
    return [row for row in rows if isinstance(row, dict)]


# How many versions have been checked against the one before (each upload that replaced a live one, uploads.jsonl),
# at least 1: version_alert's and raise_rechecks' Bonferroni count.
def versions_checked(state: Path) -> int:
    rows = upload_rows(state)
    return max(1, len({row["previous"] for row in rows if row.get("previous")}))


# Whether compare's longest margin fell beyond luck: 10+ pairs and the same one-sided sign test as version_alert over
# the pairs that changed (7 of 10 down alone is luck 17% of the time). Review M33: v0032 against v0030, 9.4 -> 3.8.
def margin_fell(compare: dict | None) -> bool:
    margin = (compare or {}).get("longest_margin") or {}
    return margin.get("pairs", 0) >= 10 and sign_p(margin.get("down", 0), margin.get("up", 0)) <= TRIGGER_P


# online.json's "held" after this cycle's `compare` of live version `sid`: {"versions": [previous, sid], "at"} while
# the comparison alerts (clearly_worse, margin_fell), and for prune.HOLD_S after the last cycle it did, even once
# newer versions go live; the newest alert replaces an older one. None once nothing is held. prune.old_dev_replays
# keeps the held versions' dev wins, which it otherwise deletes once a version is neither live nor the one before it
# (losses and draws it never deletes): at most two versions' DEV_REPLAYS per dev team beyond those two, 50 x 14 dev
# teams x 0.6-1 MB = 0.4-0.7 GB a version.
def hold_replays(held: dict | None, compare: dict | None, sid: int, now: float) -> dict | None:
    if compare and (clearly_worse(compare) or margin_fell(compare)):
        return {"versions": [compare["previous"], sid], "at": now}
    return held if held and now - held.get("at", 0) < prune.HOLD_S else None


def known(repo: Path, commit: str) -> bool:
    return subprocess.run(["git", "cat-file", "-e", f"{commit}^{{commit}}"], cwd=repo, capture_output=True).returncode == 0


def is_ancestor(repo: Path, commit: str, descendant: str) -> bool:
    return subprocess.run(["git", "merge-base", "--is-ancestor", commit, descendant], cwd=repo,
                          capture_output=True).returncode == 0


# The commit submission `sid` was built from, by uploads.jsonl's `rows` alone (None when they can't say): the upload
# made while `sid` was live saved that build's archive digest (previous_digest), and the latest earlier upload with
# that digest is its commit. Never a row's position: each row is written before its upload is sent, so the row before
# may be an attempt that failed or whose outcome is unknown. Used by live_commits and tools/why.py.
def upload_commit(rows: list[dict], sid: int) -> str | None:
    later = next((n for n, row in enumerate(rows) if row.get("previous") == sid), None)
    digest = later is not None and rows[later].get("previous_digest")
    return next((row["commit"] for row in reversed(rows[:later]) if digest and row.get("digest") == digest), None)


# The commits of the previous and the current live submission: the current one from publishing.json's verified record
# (only when it is this submission), the previous one from uploads.jsonl (upload_commit). Returns (previous commit,
# current commit, why not) with None commits when either can't be named.
def live_commits(state: Path, sid: int, previous_sid: int) -> tuple[str | None, str | None, str]:
    verified = read(state / "publishing.json", {}).get("verified") or {}
    if verified.get("submission_id") != sid or not verified.get("commit"):
        return None, None, f"publishing.json's verified record is submission {verified.get('submission_id')}, not {sid}"
    rows = upload_rows(state)
    commit = upload_commit(rows, previous_sid)
    if not commit:
        return None, verified["commit"], f"uploads.jsonl names no upload as submission {previous_sid}'s build"
    return commit, verified["commit"], ""


# How a kept result was accepted, for the alert.
def kept_by(row: dict) -> str:
    if row.get("identity", {}).get("same_code"):
        return "kept on the champion's own code but for the build's sonar key (referee/samecode.py)"
    if row.get("idea", {}).get("kind") in ("speed", "identical") or row.get("identity", {}).get("ok"):
        return "kept on identical decisions (its games may differ through the sonar key)"
    if (row.get("sprt", {}).get("limit_kept") or {}).get("of") == "judged":
        return f"kept by judgement on {row['sprt']['limit_kept'].get('circumstance')} (LOOP.md: Judged verdicts)"
    if row.get("idea", {}).get("kind") == "targeted":
        if (row.get("sprt", {}).get("limit_kept") or {}).get("of") == "gain":
            return (f"kept as a gain on probability, its target not shown "
                    f"({(row.get('target') or {}).get('line', 'no target recorded')})")
        how = " on probability" if row.get("sprt", {}).get("limit_kept") else ""
        return f"kept on no loss{how} and its target ({(row.get('target') or {}).get('line', 'no target recorded')})"
    if row.get("idea", {}).get("kind") in meta.NONINFERIOR_KINDS:
        return "kept on no loss" + (" (a formula)" if row["idea"]["kind"] == "formula" else "")
    return "kept on H1" if row.get("sprt", {}).get("decision") == "H1" else "kept on probability"


# Method M62: every rotation opponent but tradedown rams with our own code, so a change to how we meet rams can pass
# locally and fall online (E0239, v0051). So a re-check raise_rechecks queues plays RECHECK_SHARE of all its trials
# against the gauntlet rammer tradedown (meta.MIX_OPPONENTS), with the result's own map mix. tradedown also holds a
# rotation seat, whose trials the mix adds to, so recheck_mix sets the mix share that makes the total RECHECK_SHARE
# (0.28 with driver.ROTATION's five seats and the champion's). A total of a third isn't reachable: the smallest mix
# meta.opponent_mix_ok takes (meta.MAP_SHARE) already gives 0.375.
RECHECK_SHARE = 0.4


def recheck_mix() -> dict:
    import driver  # driver imports this module
    seat = driver.ROTATION.count("tradedown") / (len(driver.ROTATION) + 1)  # trial_order: the champion, then each seat
    return {"tradedown": round((RECHECK_SHARE - seat) / (1 - seat), 3)}


# Review M33: when the live version's longest margin falls against the previous one in the same matchups
# (margin_fell), every change kept between the two live builds is a suspect, the fall measured evidence of harm: a
# todo switch-off re-check of one (source recheck:E####, or the waiting idea a review merged it into) goes to priority
# 1 with the compare numbers in its history, and one with none waiting (todo or being built; a dropped or played one
# doesn't count: I937 for E0331 was dropped) gets one queued at priority 1 (driver.queue_recheck, the numbers its
# evidence, recheck_mix its opponents), so it runs next, but only when the fall's p is at most TRIGGER_P over
# versions_checked (version_alert's bar: each upload is another check, so luck alone gives up to that many TRIGGER_P
# falls); below it the alert names the suspects for the orchestrator. Not for a change kept on identical decisions or
# the champion's own code (nothing to switch off) or a formula (a review's, on off-generator evidence; named "no
# re-check queued" for the orchestrator to judge). Once per (previous, current) pair, remembered in report["margin_rechecks"]; returns the alert line it wrote,
# or None.
def raise_rechecks(repo: Path, state: Path, report: dict) -> str | None:
    compare, sid = named_view(report.get("compare")), report.get("submission_id")
    if not sid or not margin_fell(compare):
        return None
    done = report.setdefault("margin_rechecks", {})
    key = f"{compare['previous']}-{sid}"
    if key in done:
        return None
    margin = compare["longest_margin"]
    opponents = ", ".join(f"team {o['opponent']} sub {o['submission']}: {o['pairs']}" for o in compare.get("opponents", []))
    numbers = (f"submission {sid} against {compare['previous']} on the same named opponent versions, maps, starts and "
               f"pool periods (M73's inferred pairs left out): longest margin {margin['before']} -> {margin['after']} "
               f"(down in {margin['down']}, up in {margin['up']} of "
               f"{margin['pairs']} pairs, sign test p {sign_p(margin['down'], margin['up']):.2f}; "
               f"{len(compare.get('opponents', []))} opponents: {opponents or 'not recorded'})")
    previous, current, why = live_commits(state, sid, compare["previous"])
    if previous and not (known(repo, previous) and known(repo, current)):
        previous, why = None, f"commit {previous[:7]} or {current[:7]} isn't in {repo}"
    if not previous:
        done[key] = {"at": time.time(), "error": why}
        line = f"online: {numbers}; can't name the changes between the two builds ({why}): find them by hand and tell sig"
        meta.alert(state, line)
        return line
    path = state / "results.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []
    kept = [r for r in rows if r.get("verdict") == "kept" and r.get("candidate")
            and is_ancestor(repo, r["candidate"], current) and not is_ancestor(repo, r["candidate"], previous)]
    notes, fresh = [], []
    count = versions_checked(state)
    p, bar = sign_p(margin["down"], margin["up"]), TRIGGER_P / count
    waiting = lambda idea: idea.get("status") == "todo" or str(idea.get("status", "")).startswith("trying")
    with locked(state / "ideas.jsonl"):
        ideas = [json.loads(line) for line in (state / "ideas.jsonl").read_text().splitlines() if line.strip()]
        by_id = {i["id"]: i for i in ideas}
        changed = False
        for row in kept:
            own = [i for i in ideas if i.get("source") == f"recheck:{row['id']}"]
            # A re-check a review merged into another idea is raised there.
            merged = [by_id.get(meta.merge_target(i["id"], by_id)) for i in own if i.get("status") == "merged"]
            checks = [i for i in own + merged if i and waiting(i)]
            todo = [i for i in checks if i.get("status") == "todo"]
            for idea in todo:
                if idea.get("priority") == 1:
                    notes.append(f"{row['id']} ({kept_by(row)}): re-check {idea['id']} already priority 1")
                    continue
                idea.setdefault("history", []).append(
                    f"online {time.strftime('%Y-%m-%d %H:%M')}: raised to priority 1 from {idea.get('priority')}, "
                    f"{row['id']} being one of the changes between the live builds {previous[:7]} and "
                    f"{current[:7]}: " + numbers)
                idea["priority"] = 1
                changed = True
                notes.append(f"{row['id']} ({kept_by(row)}): re-check {idea['id']} raised to priority 1")
            switchable = (not kept_by(row).startswith(("kept on identical", "kept on the champion's own code"))
                          and row.get("idea", {}).get("kind") != "formula")
            if not checks and switchable and p <= bar:
                fresh.append(row)
            elif not checks and switchable:
                notes.append(f"{row['id']} ({kept_by(row)}): no re-check queued, p {p:.3f} not under {bar:.3f} "
                             f"({TRIGGER_P:g} over {count} versions checked); the orchestrator decides")
            elif not todo:
                status = ", ".join(f"{i['id']} {i.get('status', '?')}" for i in checks)
                notes.append(f"{row['id']} ({kept_by(row)}): " + (f"no re-check to do ({status})" if checks else
                                                                   "no re-check queued"))
        if changed:
            temporary = state / "ideas.jsonl.tmp"
            temporary.write_text(meta.ideas_text(ideas))
            temporary.replace(state / "ideas.jsonl")
    import driver  # driver imports this module
    for row in fresh:  # after the write above, which would drop them; queue_recheck takes the lock itself
        evidence = (f"the online compare fell after it went live ({numbers}); {RECHECK_SHARE:.0%} of all its trials, "
                    "its rotation seat's included, are against tradedown, which rams unlike our own code (method M62)")
        added = driver.queue_recheck(row, evidence, recheck_mix(), state)
        notes.append(f"{row['id']} ({kept_by(row)}): re-check {', '.join(added) or 'refused by valid_proposals'} "
                     f"queued at priority 1 with {RECHECK_SHARE:.0%} of its trials against tradedown")
    done[key] = {"at": time.time(), "previous_commit": previous, "commit": current, "kept": [r["id"] for r in kept]}
    line = (f"online: {numbers}. Kept between {previous[:7]} (submission {compare['previous']}, from uploads.jsonl) and "
            f"{current[:7]} (submission {sid}, from publishing.json): " + ("; ".join(notes) or "no kept result")
            + (". Find out why local tests missed it (CLAUDE.md, Question the tests) and tell sig" if p <= bar else
               f". p {p:.3f} is not under {bar:.3f}, so wait for a confirming sample before looking for why local "
               "tests missed it; tell sig it may be luck"))
    meta.alert(state, line)
    return line


def active_submission() -> dict | None:
    return next((s for s in ladder.items(ladder.get("/submissions"), "submissions") if s.get("status") == "active"), None)


# The replay tools' summary of one saved replay; raises when the replay fails their consistency checks.
def summarize(repo: Path, path: Path) -> dict:
    result = subprocess.run([sys.executable, str(repo / "tools/replays.py"), "summary", str(path)],
                            cwd=repo, capture_output=True, text=True, check=True, timeout=120)
    summary = json.loads(result.stdout)
    if not summary.get("checks", {}).get("ok"):
        raise RuntimeError(f"replay {path.name} failed consistency checks")
    return summary


# Whether a saved game's summary predates what a report reads from it: `at400` (2026-09-26), which `compare` pairs the
# previous version's games on, `pearlRace` by path (`raceBy`; M78, M95) and `sprintKinds` (M77) and `portals` (M80),
# which rule-counts.md and study.md read, or, in a game that went to the round limit, `king` (review M52) with its
# `build` (method M99), which round500 reads.
def stale_summary(game: dict) -> bool:
    summary = game["summary"]
    team = summary.get("teams", {}).get("A", {})
    return ("at400" not in team or team.get("raceBy") != "path" or "portals" not in team
            or summary.get("result", {}).get("endReason") == "roundLimit"
            and ("king" not in team or isinstance(team["king"], dict) and "build" not in team["king"]))


SUMMARY_BUDGET_S = 120  # refresh_summaries' time a cycle: 25 summaries at up to summarize's 120 s each held an
# upkeep tick up to 17 min (batch-health, 2026-09-30); the rest wait for the next cycle


# Re-reads up to `limit` saved games of these submissions whose summary is stale (stale_summary), so a report needn't
# wait a version for games summarised by the current replay tools, starting none once `budget` seconds have gone. A
# game that fails is marked and left.
def refresh_summaries(repo: Path, state: Path, submissions: list[int], limit: int = 25,
                      budget: float = SUMMARY_BUDGET_S) -> None:
    deadline = time.monotonic() + budget
    for submission in submissions:
        path = state / "online-replays" / str(submission) / "index.json"
        saved = read(path, None)
        if not saved:
            continue
        changed = False
        for game in saved["games"]:
            if limit <= 0 or time.monotonic() >= deadline:
                break
            if not stale_summary(game) or game.get("refresh_failed") or not Path(game["replay"]).exists():
                continue
            limit -= 1
            changed = True
            try:
                game["summary"] = summarize(repo, Path(game["replay"]))
            except Exception as error:
                game["refresh_failed"] = f"{type(error).__name__}: {error}"[:200]
        if changed:
            save(path, saved)


DOOM_TRIES = 3  # refresh_dooms tries a replay the doom tool fails on this many cycles, then gives it up


# The P.foldRound of the build submission `sid` came from (publishing.json's verified record, only when it is this
# submission), read from that commit's bot/params.hpp, else None: the round from which the doom tool labels a feed
# (`--feed-from`), so tuning foldRound can't leave online rows labelled from the old round.
def live_fold_round(repo: Path, state: Path, sid: int) -> int | None:
    verified = read(state / "publishing.json", {}).get("verified") or {}
    if verified.get("submission_id") != sid or not verified.get("commit"):
        return None
    shown = subprocess.run(["git", "show", f"{verified['commit']}:bot/params.hpp"], cwd=repo, capture_output=True,
                           text=True)
    found = re.search(r"\bfoldRound\s*=\s*(\d+)", shown.stdout) if shown.returncode == 0 else None
    return int(found[1]) if found else None


# Adds to up to `limit` saved games of `submission` without one their deaths by how they came about (tools/watch.py
# doom --json's kinds: place and trigger, ours and theirs, MISSES 64), as "doom", for online.md's death table
# (doom_text), with the tool's DOOM_VERSION ("doom_version"): a row from an older version is read again. A game whose
# replay fails keeps "doom_failed" ({version, tries, error}) and is tried again on later cycles, up to DOOM_TRIES
# times a version, then given up (health's `death tables` alerts once for it). Feeds are labelled from the live
# build's foldRound (live_fold_round; the tool's FEED_FROM when it can't be read).
def refresh_dooms(repo: Path, state: Path, submission: int, limit: int = 40) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import watch

    path = state / "online-replays" / str(submission) / "index.json"
    saved = read(path, None)
    if not saved:
        return
    changed, fold = False, None
    for game in saved["games"]:
        if limit <= 0:
            break
        failed = game.get("doom_failed") if isinstance(game.get("doom_failed"), dict) else {}
        tries = failed.get("tries", 0) if failed.get("version") == watch.DOOM_VERSION else 0
        if (game.get("doom_version") == watch.DOOM_VERSION or tries >= DOOM_TRIES or not game.get("side")
                or not Path(game["replay"]).exists()):
            continue
        limit -= 1
        if not changed:
            fold = live_fold_round(repo, state, submission) or watch.FEED_FROM
        changed = True
        try:
            result = subprocess.run([sys.executable, str(repo / "tools/watch.py"), "doom", "--json", "--team",
                                     game["side"], "--feed-from", str(fold), game["replay"]], cwd=repo,
                                    capture_output=True, text=True, check=True, timeout=120)
            if not result.stdout.strip():  # an unreadable replay: the tool says why on stderr's first line
                raise ValueError((result.stderr.strip().splitlines() or ["no output"])[0])
            game.update(doom=json.loads(result.stdout)["kinds"], doom_version=watch.DOOM_VERSION)
            game.pop("doom_failed", None)
        except Exception as error:
            game["doom_failed"] = {"version": watch.DOOM_VERSION, "tries": tries + 1,
                                   "error": f"{type(error).__name__}: {error}"[:200]}
    if changed:
        save(path, saved)


# Adds to saved ladder and autoscrim games of `submission` chosen blind to their result (sample_games, dev games
# apart) their rams both ways (tools/watch.py ram_rows: victim gain at the start, head edge estimated from the
# round-start frame, swing, steps), as "rams" {"ours", "theirs" (theirs on us), "version": RAM_VERSION}, for
# rulecounts' and study.md's online ram cells (method M85: local cells are read at local contact density and flip
# sign online). About 1-2.5 s a replay (a watch.watch read; 12 of 11049's sample games took 11.6 s on 2026-09-29 and
# gave 238 rams ours, 232 theirs), so at most `limit` a cycle and none past `deadline_s`; a replay that fails is marked
# with its error at this version and not tried again (health.online_rams alerts when the sample gets none). A
# version's 300 sample games take about 15 cycles.
RAM_VERSION = 1


def refresh_rams(state: Path, submission: int, dev: set[int], limit: int = 20, deadline_s: float = 30) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import watch

    path = state / "online-replays" / str(submission) / "index.json"
    saved = read(path, None)
    if not saved:
        return
    changed, start = False, time.monotonic()
    for game in sample_games(saved["games"]):
        if limit <= 0 or time.monotonic() - start > deadline_s:
            break
        if ((game.get("rams") or {}).get("version") == RAM_VERSION or against_dev(game, dev)
                or game.get("side") not in ("A", "B") or not Path(game.get("replay") or "").is_file()):
            continue
        limit -= 1
        changed = True
        try:
            w = watch.watch(Path(game["replay"]))
            other = "B" if game["side"] == "A" else "A"
            game["rams"] = {"ours": watch.ram_rows(w, game["side"]), "theirs": watch.ram_rows(w, other),
                            "version": RAM_VERSION}
        except Exception as error:
            game["rams"] = {"version": RAM_VERSION, "error": f"{type(error).__name__}: {error}"[:200]}
    if changed:
        save(path, saved)


# online.md's death table (MISSES 64; meta.py adds it after evidence_text, which coding prompts get too): online.json's
# live version's saved games chosen blind to their result (sample_games) with a current "doom" (refresh_dooms), dev
# games apart, per map: each death ours against the opponent's by place and trigger, per game (tools/watch.py
# doom_table). A report, never a gate.
def doom_text(state: Path) -> str:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import watch

    report = read(state / "online.json", {})
    if not report.get("submission_id"):
        return ""
    dev = dev_ids(report)
    games = [g for g in sample_games(report.get("games", [])) if g.get("doom_version") == watch.DOOM_VERSION]
    lines = ["deaths (MISSES 64): how our deaths and the opponent's came about in this version's saved games chosen "
             "blind to their result, per map (tools/watch.py doom). " + watch.DOOM_TABLE_NOTE + " Online replays "
             "carry no debug lines, so a feed (feed?), a cap feed before foldRound (capfeed?, M63) and a split-out's "
             "parent are told by their moves. "
             f"{len(games)} games have it (the rest are read a few a cycle)."]
    for label, part in (("ladder and autoscrim", [g for g in games if not against_dev(g, dev)]),
                        ("dev", [g for g in games if against_dev(g, dev)])):
        rows = [(g["summary"]["map"]["name"] if g.get("summary") else str(g.get("map_id")), g["doom"]) for g in part]
        if rows:
            lines += [f"{label} games:", *watch.doom_table(rows, top=6)]
    return "\n".join(lines) + "\n"


BCEVAL_TRIES = 3  # refresh_bceval tries a replay bceval fails on this many cycles a version, then gives it up


def bceval_module():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import bceval_read
    return bceval_read


# Adds bceval's turning point (bceval_read.game: bceval_read.TURN) to up to `limit` saved losses and draws of
# `submission` without a current one, as "bceval" ({"v": bceval commit and reader, "turn", "rates"}), for online.md's
# turning-point table (bceval_text); returns them by game id, for the cycle's report. A replay bceval fails on keeps
# {"v", "error", "tries"} and is tried again on later cycles, up to BCEVAL_TRIES times a version. Each reading is
# cached per replay (bceval_read.CACHE) and costs about 0.1-0.3 s at nice 19 (the child is killed after
# bceval_read.CHILD_TIMEOUT); none starts after `deadline_s`, so the rest wait for the next cycle. When bceval can't
# run at all (bceval_sync.ready: a clone off the approved commit, a changed fingerprint), it raises that reason
# without reading a game or counting a try (the cycle alerts it once). A report, never a gate.
def refresh_bceval(state: Path, submission: int, limit: int = 40, deadline_s: float = 60) -> dict[int, dict]:
    bceval_read = bceval_module()
    if not bceval_read.enabled():
        return {}
    path = state / "online-replays" / str(submission) / "index.json"
    saved = read(path, None)
    if not saved:
        return {}
    clone, why = bceval_read.sync_module().ready(state)
    if clone is None:
        raise bceval_read.BcevalError(why)
    version, fresh_by_id, until = bceval_read.version(state), {}, time.monotonic() + deadline_s
    for game in saved["games"]:
        if limit <= 0 or time.monotonic() > until:
            break
        stored = game.get("bceval") or {}
        current = stored.get("v") == version
        if (not game.get("side") or "summary" not in game or game_score(game) == 1
                or not Path(game.get("replay") or "").is_file()
                or current and (not stored.get("error") or stored.get("tries", 0) >= BCEVAL_TRIES)):
            continue
        limit -= 1
        fresh = bceval_read.game(Path(game["replay"]), game["side"], state) or {}
        if fresh.get("error"):
            fresh["tries"] = stored.get("tries", 0) + 1 if current else 1
        game["bceval"] = fresh_by_id[game["id"]] = fresh
    if fresh_by_id:
        save(path, saved)
    return fresh_by_id


# online.md's turning-point table (meta.prepare_review adds it after doom_text): bceval's turning points
# (refresh_bceval) in the live version's saved losses and draws, those kept because we lost too (it describes losses,
# not how often we lose), dev games apart, by map and opponent (bceval_read.section, which states how far to trust
# it). "" while bceval is off. A report only.
def bceval_text(state: Path) -> str:
    bceval_read = bceval_module()
    report = read(state / "online.json", {})
    if not report.get("submission_id") or not bceval_read.enabled():
        return ""
    dev = dev_ids(report)
    games = [g for g in report.get("games", []) if g.get("bceval") and "summary" in g and game_score(g) < 1]
    lines = [f"turning points (bceval): {len(games)} of this version's saved losses and draws have one (the rest are "
             "read a few a cycle). They include replays kept because we lost (online.selected), so a row's games are "
             "no loss rate: compare the groups within a row."]
    for label, part in (("ladder and autoscrim", [g for g in games if not against_dev(g, dev)]),
                        ("dev", [g for g in games if against_dev(g, dev)])):
        entries = [({"map": g["summary"]["map"]["name"], "opponent": g.get("opponent")}, g["bceval"]) for g in part]
        if entries:
            lines += [f"{label} games:", *bceval_read.section(entries, ("map", "opponent"), "losses and draws")]
    return "\n".join(lines) + "\n"


# The opponent in a battle's detail (its "match", the game it was read by; a battle's games share their two teams),
# or None when the detail names neither side as ours.
def battle_opponent(detail: dict, team_id: int) -> int | None:
    match = detail.get("match", {})
    for side, other in (("A", "B"), ("B", "A")):
        if match.get(f"team{side}Id") == team_id:
            return match.get(f"team{other}Id")
    return None


# The winning side of a game in the API's match record: "A", "B", or None for a draw. The site writes "a" or "b" (its
# battle pages, as tools/replays.py's scout index shows); a record without the field, or with any other value,
# raises, so the game is retried and then left in `invalid` rather than scored as a draw.
def api_winner(match: dict) -> str | None:
    if "winner" not in match:
        raise ValueError("the match record has no winner")
    winner = match["winner"]
    if isinstance(winner, str) and winner.upper() in ("A", "B"):
        return winner.upper()
    if winner is None or winner == "draw":
        return None
    raise ValueError(f"unreadable winner {winner!r}")


# A game's API result from its battle's detail alone, without reading the game's own record: the detail's "match"
# gives the teams and sides, which a battle's games share (every one of the 600+ battles saved in online-replays by
# 2026-09-27, 3,900 games), our submission (the detail's field, else our request row for the game: our_submission,
# `requested`), and the game's row in "games" its winner and map name, whose id is in `map_ids` (name -> id,
# map_ids_by_name). None when the detail names neither side as ours, neither names our submission, the map isn't in
# `map_ids` (a retired map, or a name two maps share) or the winner is unreadable: then the game's record, which
# carries its mapId, is read instead.
def battle_result(detail: dict, team_id: int, game: dict, map_ids: dict[str, int],
                  requested: dict[int, int] | None = None) -> dict | None:
    match = detail.get("match", {})
    side = next((s for s in ("A", "B") if match.get(f"team{s}Id") == team_id), None)
    if not side or game.get("mapName") not in map_ids:
        return None
    ours, source = our_submission({"our_submission": match.get(f"submission{side}Id")}, game["id"], requested or {})
    if not ours:
        return None
    try:
        winner = api_winner(game)
    except ValueError:
        return None
    them = "B" if side == "A" else "A"
    return {"id": game["id"], "submission_id": ours, "submission_from": source,
            "opponent": match.get(f"team{them}Id"), "opponent_submission_id": match.get(f"submission{them}Id"),
            "side": side, "map_id": map_ids[game["mapName"]], "map_hash": None, "ranked": bool(match.get("ranked")),
            "requested_at": match.get("requestedAt"), "winner": winner, "from": "battle"}


# Map name -> id from GET /maps, for battle_result, leaving out every name more than one map has: from 2026-09-27 the
# organisers may add variants of a map (same kelp and portals, other pearl timings), possibly under its name, and a
# game on one must not be filed under the other's id.
def map_ids_by_name(maps: list[dict]) -> dict[str, int]:
    ids: dict[str, set[int]] = defaultdict(set)
    for m in maps:
        if m.get("name"):
            ids[m["name"]].add(m["id"])
    return {name: next(iter(found)) for name, found in ids.items() if len(found) == 1}


def collect_games(repo: Path, state: Path, team_id: int, sid: int, details: list[dict],
                  dev: set[int] = frozenset(), map_ids: dict[str, int] | None = None) -> list[dict]:
    """Record every finished game (FINISHED: a draw too) our team played (requested, autoscrim or challenged), each
    filed under the submission that played it, so a version's games aren't lost when the next one goes live; returns
    the active submission's saved games. Our submission is the record's field, else the replay's (replay_submission;
    a non-dev game's replay is read first) or our request row's (requested_submissions), each row saying which
    ("submission_from": "api", "replay" or "request"); a game none names is retried as a bad one. Preserves the
    opponent's version while the site names it (None since API_GONE), map, mode and side. A game is saved with its
    replay and summary ("games" in the version's index), except a game against a dev team (`dev`, team ids) once that version
    has DEV_REPLAYS of it or this call's REPLAYS_PER_CYCLE are spent, unless it is a loss or draw within
    DEV_LOSS_REPLAYS (`keeps`: saved "kept": "loss", up to LOSS_REPLAYS_PER_CYCLE a call besides the others; one that
    waits is listed in the index's "owed" and stays a loss replay, never the sample's): that keeps only its API result
    ("results"), which game_score reads the same way, from its battle's detail when it can (battle_result,
    `map_ids`; no API call), else from its record. So every dev game is recorded the cycle its battle's detail shows
    it finished, however many come back. Reads at most RESULTS_PER_CYCLE game records, REPLAYS_PER_CYCLE replays and
    LOSS_REPLAYS_PER_CYCLE loss replays a call; the other games wait for the next. A 429 (or its Retry-After still
    running, ladder.quiet) stops the reads until the next call, and isn't a failed attempt. Rows against dev teams are
    marked "dev". A version's index grows with its time live: its ladder games' summaries, at most DEV_REPLAYS +
    DEV_LOSS_REPLAYS a dev team, and a result row (about 300 bytes) for each later dev game, at most
    DEV_GAMES_AN_HOUR an hour (about 360 KB a day). A dev game given up after three failed reads keeps its result row once its winner was read,
    so a failing loss replay never drops a loss from the record."""
    map_ids = map_ids or {}
    indexes: dict[int, dict] = {}

    def index(submission: int) -> dict:
        if submission not in indexes:
            cached = read(state / "online-replays" / str(submission) / "index.json", {"games": []})
            indexes[submission] = {"games": {g["id"]: g for g in cached["games"]},
                                   "results": {g["id"]: g for g in cached.get("results", [])},
                                   "invalid": cached.get("invalid", {}), "owed": set(cached.get("owed", []))}
        return indexes[submission]

    def store(submission: int) -> None:
        folder = state / "online-replays" / str(submission)
        folder.mkdir(parents=True, exist_ok=True)
        saved = index(submission)
        save(folder / "index.json", {"games": list(saved["games"].values()), "invalid": saved["invalid"],
                                     **({"results": list(saved["results"].values())} if saved["results"] else {}),
                                     **({"owed": sorted(saved["owed"])} if saved["owed"] else {})})

    known = {int(p.parent.name) for p in (state / "online-replays").glob("*/index.json") if p.parent.name.isdigit()}
    seen = set()
    for submission in known | {sid}:
        seen |= set(index(submission)["games"]) | set(index(submission)["results"])
    active = index(sid)
    fetched = downloaded = losses = 0
    changed: set[int] = set()

    # The replays `owner` has saved of games against `opponent` as its sample (`loss` False) or as losses past it.
    def sampled(owner: int | None, opponent: int | None, loss: bool = False) -> int:
        return sum(g["opponent"] == opponent and (g.get("kept") == "loss") == loss
                   for g in index(owner)["games"].values()) if owner else 0

    # What dev game `gid` against `opponent` by `owner` keeps: "sample", a replay in the sample while it has room and
    # this call's replays last (whatever the game's result, so the sample stays unbiased); "loss", a replay of a loss or
    # draw (`lost`) while DEV_LOSS_REPLAYS last, at most DEV_LOSS_REPLAYS a team per version in all; else "result", its
    # API result only. A game once judged "loss" is listed in its index's "owed" until saved, and stays "loss" (or
    # "result" once the losses are full): while wins past the sample are filed from their battle's detail, a loss
    # waiting on a record read or LOSS_REPLAYS_PER_CYCLE must never enter the sample next call and tilt it.
    def keeps(gid: int, owner: int | None, opponent: int | None, lost: bool) -> str:
        owed = index(owner)["owed"] if owner else set()
        if gid not in owed and downloaded < REPLAYS_PER_CYCLE and sampled(owner, opponent) < DEV_REPLAYS:
            return "sample"
        if (lost or gid in owed) and sampled(owner, opponent, loss=True) < DEV_LOSS_REPLAYS:
            if owner and gid not in owed:
                owed.add(gid)
                changed.add(owner)
            return "loss"
        return "result"

    requested = requested_submissions(state)
    # Our submissions the loop knows of, the only ones a replay's bot name may name (replay_submission).
    ours = known | {sid} | set(requested.values())
    # Replays read before the version they belong to is known (replay_submission): no index, so no reader's folder.
    incoming = state / "online-replays" / "incoming"

    def stage(path: Path) -> None:  # downloads game `path.stem`'s replay to `path` once, whole or not at all
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            time.sleep(0.6)
            part = path.with_suffix(".part")
            part.write_bytes(ladder.download(f"/battles/{path.stem}/replay"))
            part.replace(path)

    def unowe(gid: int) -> None:  # game `gid` is no longer owed by any loaded index (whichever version judged it)
        for submission, saved in indexes.items():
            if gid in saved["owed"]:
                saved["owed"].discard(gid)
                changed.add(submission)

    stopped = ladder.quiet()
    for detail in details:
        opponent = battle_opponent(detail, team_id)
        for game in detail.get("games", []):
            gid = game["id"]
            if gid in seen or game.get("status") not in FINISHED:
                continue
            previous = active["invalid"].get(str(gid), {})
            if previous.get("attempts", 0) >= 3 or time.time() < previous.get("retry_after", 0):
                continue
            known = None  # a dev game's result row once read, kept as a result if its replay is given up
            if opponent in dev and (row := battle_result(detail, team_id, game, map_ids, requested)):
                known = {**row, "dev": True}
                kept = keeps(gid, row["submission_id"], row["opponent"], game_score(row) < 1)
                if kept == "result":
                    index(row["submission_id"])["results"][gid] = {**row, "dev": True}
                    index(row["submission_id"])["owed"].discard(gid)
                    seen.add(gid)
                    active["invalid"].pop(str(gid), None)
                    changed.update((row["submission_id"], sid))
                    continue
                if kept == "loss" and losses >= LOSS_REPLAYS_PER_CYCLE:
                    continue  # owed: saved next call, without spending a record read
            if game.get("hasReplay") is False:
                continue  # finished before its replay exists: read next call, no attempt spent
            # Past a 429, or the record budget, the rest wait; so does a non-dev game that will need a replay once this
            # call's are spent, without spending a record read.
            if (stopped or fetched >= RESULTS_PER_CYCLE
                    or opponent is not None and downloaded >= REPLAYS_PER_CYCLE and opponent not in dev):
                continue
            fetched += 1
            staged = incoming / f"{gid}.replay"
            try:
                time.sleep(0.6)
                match = match_for_team(ladder.get(f"/battles/{gid}"), team_id)
                if not match:
                    continue
                them = "B" if match["side"] == "A" else "A"
                owner, source = our_submission(match, gid, requested)
                # A non-dev game keeps its replay anyway, and its replay names our submission (replay_submission);
                # so does the replay of a game neither the record nor a request row places.
                if source != "api" and (match[f"team{them}Id"] not in dev or owner is None):
                    stage(staged)
                    if found := replay_submission(staged, match["side"], ours):
                        owner, source = found, "replay"
                if owner is None:
                    raise ValueError("no source names our submission (record, replay or request row)")
                row = {"id": gid, "submission_id": owner, "submission_from": source,
                       "opponent": match[f"team{them}Id"], "opponent_submission_id": match.get(f"submission{them}Id"),
                       "side": match["side"], "map_id": match.get("mapId"), "map_hash": match.get("mapHash"),
                       "ranked": match["ranked"], "requested_at": match.get("requestedAt")}
                kept = None
                if row["opponent"] in dev:
                    row["dev"] = True
                    try:  # an unreadable winner wants the replay, whose summary names it
                        known = {**row, "winner": api_winner(match)}
                        lost = known["winner"] != match["side"]
                    except ValueError:
                        lost = True
                    kept = keeps(gid, owner, row["opponent"], lost)
                if kept == "result":
                    index(owner)["results"][gid] = {**row, "winner": api_winner(match)}
                    index(owner)["owed"].discard(gid)
                else:
                    if (losses >= LOSS_REPLAYS_PER_CYCLE if kept == "loss" else downloaded >= REPLAYS_PER_CYCLE):
                        continue
                    if kept == "loss":
                        row["kept"] = "loss"
                        losses += 1
                    else:
                        downloaded += 1
                    stage(staged)
                    found = replay_submission(staged, row["side"], ours) if source == "request" else None
                    if found not in (None, owner):
                        owner, source = found, "replay"  # the file itself wins over our request row
                        row.update(submission_id=owner, submission_from=source)
                    folder = state / "online-replays" / str(owner)
                    folder.mkdir(parents=True, exist_ok=True)
                    path = folder / f"{gid}.replay"
                    staged.replace(path)
                    # The API's winner beside the replay's checks the results kept without one (winner_check).
                    index(owner)["games"][gid] = {**row, "replay": str(path), "summary": summarize(repo, path),
                                                  **start_fields(path, row["side"]),
                                                  **({"api_winner": match["winner"]} if "winner" in match else {})}
                    unowe(gid)  # the request row's version may have judged it owed before the replay moved it
                seen.add(gid)
                active["invalid"].pop(str(gid), None)
                changed.add(owner)
            except Exception as error:
                if ladder.limited(error):  # the site's rate limit, not this game's fault: stop, and read it next call
                    stopped = True
                    continue
                active["invalid"][str(gid)] = {"reason": f"{type(error).__name__}: {error}"[:500],
                                               "attempts": previous.get("attempts", 0) + 1,
                                               "retry_after": time.time() + 3600}
                if previous.get("attempts", 0) + 1 >= 3:  # given up (recorded_ids counts it): no longer owed
                    unowe(gid)
                    if known:  # only the replay is lost: its result still counts in all_games (MISSES 55)
                        index(known["submission_id"])["results"][gid] = known
                        seen.add(gid)
                        changed.add(known["submission_id"])
            finally:
                staged.unlink(missing_ok=True)  # read, or saved (moved) under its version
            changed.add(sid)
    # Each index written once a call, not once a game: a version's index holds hundreds of summaries.
    for submission in changed:
        store(submission)
    return list(active["games"].values())


# How often the API's winner (api_winner) agrees with the replay's over a version's saved games against dev teams that
# carry both: {"checked", "agree"}. Results kept without a replay trust the API's winner, so a disagreement alerts
# (health.dev_battles).
def winner_check(games: list[dict], dev: set[int]) -> dict:
    checked = agree = 0
    for game in games:
        if game.get("opponent") in dev and "api_winner" in game:
            checked += 1
            try:
                agree += api_winner({"winner": game["api_winner"]}) == game["summary"]["result"]["winner"]
            except ValueError:
                pass
    return {"checked": checked, "agree": agree}


# Ids of every game an online-replays index records (saved, a result, or given up after three failed attempts), so a
# battle whose finished games are all here needs no further look (settled_battles).
def recorded_ids(state: Path) -> set[int]:
    ids: set[int] = set()
    for path in (state / "online-replays").glob("*/index.json"):
        saved = read(path, {})
        ids.update(g["id"] for g in saved.get("games", []) + saved.get("results", []))
        ids.update(int(gid) for gid, bad in saved.get("invalid", {}).items() if bad.get("attempts", 0) >= 3)
    return ids


# Whether battle `detail` needs no more reads: every game finished and every finished one (FINISHED) recorded.
def battle_settled(detail: dict, recorded: set[int]) -> bool:
    return all(g.get("status") not in PLAYING and (g.get("status") not in FINISHED or g.get("id") in recorded)
               for g in detail.get("games", []))


# The battles in the list (`battles`) whose detail needs no more reads: those settled before, and those whose
# `details` (battle id -> detail read this cycle) are settled. Only ids still in the list are kept, so the record
# stays within BATTLE_WINDOW ids; one that leaves the list unsettled is carried by owed_battles.
def settled_battles(battles: list[dict], details: dict[int, dict], before: set[int], recorded: set[int]) -> list[int]:
    return [b["id"] for b in battles
            if b["id"] in before or b["id"] in details and battle_settled(details[b["id"]], recorded)]


# online.json's "behind" (battle id -> when it was first unsettled) and "closed" (battle id -> {"at", "why"}: battles
# never to read again though a game is in no index) from the last cycle's, and the alert lines of those given up.
# Behind: the battles of `details` (read this cycle) not settled, and the carried ones this cycle didn't read (past
# CARRIED_PER_CYCLE, a 429, a failed read). The next cycle reads them by id whether or not the list still shows them:
# the list holds only our newest BATTLE_WINDOW battles, and a cycle records at most REPLAYS_PER_CYCLE replays, newest
# first, so on 2026-09-26/27, with dev battles back to back, about 270 finished ladder games the loop had requested
# left the list before their turn and were never recorded (MISSES 83). One still behind BEHIND_S after it was first
# seen is closed "given up" and alerted, since its games are lost; one read settled with a game that never finished
# (cancelled, error: battle_settled) is closed "unfinished", so its request row doesn't bring it back each cycle.
# Closed entries go BEHIND_S after closing, past their request rows (REQUEST_ROWS_DAYS). Both hold a backlog's
# battles: tens.
def owed_battles(details: dict[int, dict], recorded: set[int], behind: dict[str, float], closed: dict[str, dict],
                 now: float) -> tuple[dict[str, float], dict[str, dict], list[str]]:
    closed = {bid: c for bid, c in closed.items() if now - c["at"] < BEHIND_S}
    for bid, detail in details.items():
        if battle_settled(detail, recorded) and any(g.get("id") not in recorded for g in detail.get("games", [])):
            closed.setdefault(str(bid), {"at": now, "why": "unfinished"})
    owed = {str(bid) for bid, detail in details.items() if not battle_settled(detail, recorded)}
    owed = (owed | {bid for bid in behind if int(bid) not in details}) - set(closed)
    kept, news = {}, []
    for bid in sorted(owed, key=int):
        if now - behind.get(bid, now) < BEHIND_S:
            kept[bid] = behind.get(bid, now)
        else:
            closed[bid] = {"at": now, "why": "given up"}
            news.append(f"online: battle {bid} still has unrecorded games {BEHIND_S // 86400} days after it was "
                        f"first read unsettled and is no longer read (online.BEHIND_S): its games are lost unless "
                        f"fetched by hand (MISSES 83)")
    return kept, closed, news


# The battles (their first game's id, which is the battle's: all 57 request rows the list of 2026-09-28 22:10 showed)
# of our request rows (match-requests.json, the last REQUEST_ROWS_DAYS: recent_rows) with a game no index records
# (`recorded`), each with its row's time: read by id like owed_battles', so a game the loop asked for is recorded even
# if it left the list before online.json carried it (the games lost on 2026-09-26/27).
def requested_battles(state: Path, recorded: set[int], now: float) -> dict[int, float]:
    return {row["ids"][0]: row.get("at", now) for row in recent_rows(read(state / "match-requests.json", []), now)
            if isinstance(row, dict) and row.get("status") == "requested" and row.get("ids")
            and any(gid not in recorded for gid in row["ids"])}


# One row per matchup (our version, opponent version, map, pool period, start, mode) with its games, wins and draws:
# the start is start_key's (where we started, since from LAYOUT_CUTOFF a side names either of two starts), and "sides"
# counts the row's games by our side.
def per_map_stats(games: list[dict]) -> list[dict]:
    groups = {}
    seen = set()
    for game in games:
        key = (game["submission_id"], game["opponent"], game["opponent_submission_id"], game["map_id"],
               game.get("map_hash"), pool_period(game), start_key(game), game["ranked"])
        group = groups.setdefault(key, {**{k: game[k] for k in ("submission_id", "opponent", "opponent_submission_id",
                                                               "map_id", "ranked")}, "pool": pool_period(game),
                                        "start": start_key(game), "sides": {}})
        group["map_hash"] = game.get("map_hash")
        group.setdefault("games", 0)
        group.setdefault("wins", 0)
        group.setdefault("draws", 0)
        if game["id"] in seen:
            continue
        seen.add(game["id"])
        group["games"] += 1
        group["sides"][game["side"]] = group["sides"].get(game["side"], 0) + 1
        winner = game["summary"]["result"]["winner"]
        group["wins"] += winner == game["side"]
        group["draws"] += winner is None
        group["independent_samples"] = "since 2026-09-25 17:00 each game has its own pearl seed, so repeats are separate but same-matchup samples; earlier repeats were identical games"
    return list(groups.values())


def evidence_text(state: Path, params: str | None = None) -> str:
    """Keep version identity and uncertainty visible in coding and meta prompts. `params` is the champion's
    params.hpp text for the round-500 section's merge floor (read from loop/champion when None)."""
    report = read(state / "online.json", {})
    if not report.get("submission_id") or report.get("error"):
        return "Online evidence unavailable or stale; see online.json. Do not infer current-version performance.\n"
    verified = read(state / "publishing.json", {}).get("verified", {})
    site = f" (the site's version {verified['site_version']}; our tag numbers can differ)" if verified.get("site_version") else ""
    identity = (f"Verified source commit: {verified['commit']}{site}. " if verified.get("submission_id") == report["submission_id"]
                else "Equivalence to the local champion is NOT established by the name/date. ")
    dev = dev_ids(report)
    recent = [{**{k: game.get(k) for k in ("id", "opponent", "opponent_submission_id", "map_id", "map_hash",
                                           "side", "ranked", "requested_at", "replay")},
               "result": game["summary"].get("result"),
               "our_behavior": game["summary"].get("teams", {}).get(game["side"])}
              for game in [g for g in report.get("games", []) if not against_dev(g, dev)][-10:]]
    same = same_play(report)[1:]
    group = (f"It builds the same code but the sonar key as {', '.join(map(str, same))} (referee/samecode.py), so "
             "their games are this version's (online.play_group). " if same else "")
    return (f"Uploaded submission {report['submission_id']}, fetched at {report.get('fetched_at')}. "
            + identity + group + "Diagnostic only. Since 2026-09-25 17:00 each match has its own pearl seed, so a repeated matchup is a new "
            "(same opponent, map and start) sample, not an identical game; before then repeats were identical.\n"
            + "compare: this version against the previous one on the same opponent versions (named, or inferred: "
            "below), maps, starts and pool "
            f"periods (`pool`: from {POOL_CUTOFF} a map's pearl timings may differ under the same id; a start is where "
            f"we started, since from {LAYOUT_CUTOFF} the site swaps the teams' start tiles in some games, so our side "
            "doesn't say; `unpairable` counts games no replay placed, `unknown_opponent` games whose opponent version "
            "the site no longer names; `inferred` pairs those by opponent team, map, start and period when no "
            "rating read shows the opponent a new bot between them (method M73), a rule that sees only a new bot's "
            "fast-moving rating, so about 38% of such pairs still meet another of the opponent's bots (measured on "
            "named games to 2026-09-28), which mostly dilutes a change, and `named` then holds the named pairs alone; "
            "`candidates` counts the matchups either version's games could pair on, paired or not); "
            "`concentration` is each side's longest dragon at round 400 and keep_pct, 100 x its longest at the round "
            "limit over its total length at round 400.\n"
            + "compare, per_map, recent_games and round500 are ladder and autoscrim games only; dev games are in the "
            "dev section.\n"
            + json.dumps({"compare": report.get("compare")}) + "\n"
            + dev_text(report) + "per_map (matchup rows added up per map):\n"
            + "\n".join(json.dumps(total) for total in map_totals(report.get("per_map", [])))
            + "\nrecent_games:\n" + "\n".join(json.dumps(game) for game in recent) + "\n"
            + round500_text(state, meta.champion_params() if params is None else params))


# One record (team_records) as a line: "7676 vs dev 4525: 9 games, 7-0-2, score 0.778 (95% 0.453-0.937), side A
# 9/9" (the side split only on records written since review M48).
def record_line(row: dict) -> str:
    low, high = row.get("ci") or rate_ci(row["wins"] + row["draws"] / 2, row["games"])  # records before 2026-09-27
    sides = ", ".join(f"{side} {n}/{row['games']}" for side, n in (row.get("sides") or {}).items())
    pool = f" ({row['pool']})" if row.get("pool") else ""  # records before 2026-09-27 21:00 AEST have none
    dev = row["dev_submission_id"] or "submission unknown (the site stopped naming it)"
    return (f"{row['submission_id']} vs dev {dev}{pool}: {row['games']} games, {row['wins']}-"
            f"{row['draws']}-{row['losses']}, score {row['score']:.3f} (95% {low:.3f}-{high:.3f})"
            + (f", side {sides}" if sides else ""))


# A map_compare's rows, each with our local score on its map as the one side all its games had us on, when they all
# did (`sides`, side_counts): "local_A" and "local_A_games" from `local` (local_sides), or "no local side-A figure".
# Rows from games on both sides, or with no `local` (an online.json before review M48), are returned as they are.
# The local report's maps are matched to site ids by the site's current map files (local_sides), so its figure
# describes the current pool period's maps: a row from another period (before the 21:00 pearl change) gets none.
def side_rows(rows: list[dict], sides: dict[str, int], local: dict | None) -> list[dict]:
    if len(sides) != 1 or local is None:
        return rows
    side = next(iter(sides))
    known = {m["map_id"]: m for m in local.get("maps", []) if m.get("map_id") is not None}
    now = pool_period({"requested_at": datetime.fromtimestamp(time.time(), UTC).isoformat()})
    out = []
    for row in rows:
        if row.get("pool", now) != now:
            out.append({**row, f"local_{side}": f"no local side-{side} figure for this pool period"})
            continue
        found = known.get(row["map_id"]) or {}
        figure = found.get(side)
        out.append({**row, **({"map": found["map"]} if found else {}),
                    **({f"local_{side}": figure["score"], f"local_{side}_games": figure["games"]} if figure
                       else {f"local_{side}": f"no local side-{side} figure"})})
    return out


# A compare's games by our side for its header: "; our side A 474/474, so each map row mixes the map with side A
# (local_A beside it)" when one side, else each side's count; "" on a compare written before review M48.
def side_text(sides: dict[str, int]) -> str:
    total = sum(sides.values())
    counts = ", ".join(f"{side} {n}/{total}" for side, n in sides.items())
    if len(sides) == 1:
        side = next(iter(sides))
        return f"; our side {counts}, so each map row mixes the map with side {side} (local_{side} beside it)"
    return f"; our side {counts}" if sides else ""


# Our games by side over dev records (record, team_records; pass each record once): {"A": n, "B": n, "unknown": n}, where
# "unknown" counts games of records written before review M48 (no `sides`) and games whose side wasn't saved.
def records_sides(records: list[dict]) -> dict[str, int]:
    total = Counter({"A": 0, "B": 0, "unknown": 0})
    for row in records:
        if "sides" not in row:
            total["unknown"] += row["games"]
        for side, n in (row.get("sides") or {}).items():
            total[side if side in ("A", "B") else "unknown"] += n
    return dict(total)


# online.md's dev section's note on sides (review M48): `sides` (records_sides over every record the section shows)
# as "340 of 340 dev games here had us as side A" or, once any has us as B, "A a/N, B b/N"; then local_sides's
# source for local_<side>, from online.json's `local_sides`. "" in an online.json written before M48 (no local).
def local_sides_text(local: dict | None, sides: dict[str, int]) -> str:
    if local is None:
        return ""
    known = sides.get("A", 0) + sides.get("B", 0)
    if not known:
        counts = "no dev game here says which side we were on"
    elif not sides.get("B"):
        counts = f"{known} of {known} dev games here had us as side A"
    else:
        counts = f"our side in the {known} dev games here that say: A {sides['A']}/{known}, B {sides['B']}/{known}"
    unknown = (f" ({sides['unknown']} more, from records written before review M48, don't say)"
               if sides.get("unknown") else "")
    head = (f"sides: the battle API can't choose a side (docs/unsw/api.md); {counts}{unknown}, so a map row from one "
            "side's games mixes the map with that side; each record gives our games by side, and a one-sided "
            "compare's map rows add local_<side>, ")
    if "maps" not in local:
        return head + f"here none: no local figure, {local.get('note')}"
    return head + (f"our uploaded commit {local['commit'][:7]}'s score on that map as that side in "
                   f"{local['experiment']}'s official-map report ({local['role']}, against {str(local['opponent'])[:7]}"
                   ", one change apart, so near self-play: under 0.5 where that side itself loses there), with its "
                   "games")


# A dev_compare as lines: per shared dev submission the maps better, worse and the same, the sign test's n and its
# one-sided chances and the games by our side, then the per-map rows (side_rows, with `local`, local_sides); or its
# note when the two versions share no dev submission.
def compare_lines(compare: dict | None, submission_id: int, local: dict | None = None) -> list[str]:
    if not compare or "by_dev_submission" not in compare:  # none, or the per-pair compare written before 2026-09-27
        return []
    if compare.get("note"):
        return [f"  {submission_id} against {compare['previous']}: {compare['note']}"]
    lines = []
    for c in compare["by_dev_submission"]:
        lines.append(f"  {submission_id} against {compare['previous']} on dev {c['dev_submission_id']}, map by map: "
                     f"better on {c['better']}, worse on {c['worse']}, same on {c['same']}; sign test n = {c['n']}, "
                     f"chance of this much better by luck {c['p_better']}, this much worse {c['p_worse']}"
                     + side_text(c.get("sides") or {}))
        lines.append("  " + json.dumps(side_rows(c["maps"], c.get("sides") or {}, local)))
    return lines


# online.md's dev-team section: what dev battles are, then the dev allowance (games left this rolling hour and when
# the next dev battle is due, dev_due), the note on our sides (local_sides_text), the benchmark's record per version of
# ours (records only within one dev submission), each dev team this version with its share and records, the
# map-matched compares, how fast dev battles come back, and how often the API's winner matched the replay's.
def dev_text(report: dict) -> str:
    bench, dev = report.get("benchmark") or {}, report.get("dev") or {}
    sid = report.get("submission_id")
    lines = ["dev teams: the organisers' bots (\"dev\": true on /leaderboard), played for as long as each submission "
             "of ours is live, one battle every dev_pace within their own hourly allowance (DEV_GAMES_AN_HOUR), to a "
             "team due a probe (unplayed for DEV_PROBE_S, or once a version after a loss to a team we beat heavily; "
             "never two probes in a row), else by share (dev_weight: near 0 for a team we always beat; LOOP.md, "
             "Dev battles). A share's score pools every version and pool period; every record is one of our versions "
             "against one dev submission (dev teams update their bot) in one pool period (pool_period), never pooled "
             "across two; score is points per "
             "game, a draw half a win, with its 95% interval (chance.rate_ci), so the range shows how many games the "
             "time allowed. Games past each team's replay sample are scored from the API's result. A report, never a "
             "gate."]
    if isinstance(dev.get("allowance"), int):
        due = dev.get("due") or 0
        lines.append(f"dev allowance: {dev['allowance']} of {DEV_GAMES_AN_HOUR} games left this rolling hour at the "
                     f"cycle's round; the next dev battle "
                     + (f"is due at {time.strftime('%H:%M:%S', time.localtime(due))}" if due > dev.get("at", 0)
                        else "was due then")
                     + f" (one every {dev_pace(dev.get('maps') or []):.0f} s, dev_due)")
    bench_id = (bench.get("team") or {}).get("id")
    shown = bench.get("records", []) + [r for t in dev.get("teams", []) if t["id"] != bench_id for r in t["records"]]
    if note := local_sides_text(report.get("local_sides"), records_sides(shown)):
        lines.append(note)
    if team := bench.get("team"):
        lines.append(f"benchmark: team {team['id']} ({team.get('name')}, Elo {team.get('elo')}; {team.get('why')}), "
                     "every version:")
        lines += ["  " + record_line(r) for r in bench.get("records", [])]
        lines += compare_lines(bench.get("compare"), sid, report.get("local_sides"))
    for team in dev.get("teams", []):
        scored = (f" (score {team['score']}, 95% lower bound {team['lower']}, over every version and pool period)"
                  if team.get("score") is not None else "")
        lines.append(f"team {team['id']} ({team.get('name')}, Elo {team.get('elo')}), share {team['weight']} from our "
                     f"latest {team['weight_games']} games against "
                     + (f"its submission {team['dev_submission_id']}" if team.get("dev_submission_id") else
                        "it (its submissions no longer named, so any of them)") + f"{scored}, "
                     f"{team.get('games', 0)} games this version"
                     + (": the benchmark, above" if team["id"] == (bench.get("team") or {}).get("id") else ":"))
        if team["id"] != (bench.get("team") or {}).get("id"):
            lines += ["  " + record_line(r) for r in team["records"]] or ["  no games yet"]
            lines += compare_lines(team.get("compare"), sid, report.get("local_sides"))
    if timing := dev.get("timing"):
        minutes = timing["minutes"]
        speed = (f"request to results median {minutes['median']} min, slowest {minutes['slowest']} min, over the "
                 f"latest {minutes['n']}" if minutes["n"] else "none timed yet")
        lines.append(f"dev battles: {timing['done_last_hour']} finished in the last hour ({timing['games_last_hour']} "
                     f"games), {timing['out']} out; {speed}; our queued dev battles have at most {dev.get('ahead', 0)} "
                     f"ahead (new ones wait above {MAX_AHEAD})")
    if check := dev.get("winner_check"):
        lines.append(f"API winners agree with the replay's on {check['agree']} of {check['checked']} saved dev games")
    return "\n".join(lines) + "\n"


# per_map's matchup rows (opponent version, map, start) added up per map and pool period (a row written before the
# period existed counts as "unknown"), so a prompt gets one line a map and period.
def map_totals(rows: list[dict]) -> list[dict]:
    totals: dict = {}
    for row in rows:
        pool = row.get("pool", "unknown")
        total = totals.setdefault((row["map_id"], pool), {"map_id": row["map_id"], "pool": pool, "matchups": 0,
                                                          "games": 0, "wins": 0, "draws": 0})
        total["matchups"] += 1
        for key in ("games", "wins", "draws"):
            total[key] += row.get(key, 0)
    return sorted(totals.values(), key=lambda total: (total["map_id"] or 0, total["pool"]))


ROUND500_VERSIONS = 5  # the latest versions of ours with round-limit games that online.md counts
ROUND500_ROWS = 10  # the most recent of their round-500 losses that online.md lists one by one


# P.mergeMinUnits from the champion's params.hpp text (merging stops once the team is down to it), or None.
def merge_floor(params: str) -> int | None:
    found = re.search(r"\bmergeMinUnits\s*=\s*(\d+)", params)
    return int(found[1]) if found else None


# The measured share of a folding dragon's drop its king eats: 0.425 of ceil(L/2), about 0.21 of the folder's length
# (rule counts of 307dd8c, n 1,702 feeds; method M74, 2026-09-29), for round500's `bound_share` (the fold-all bound
# assumes the king eats every pearl of every drop). A report constant, measured, not the bot's P.capFeedFoldShare,
# a decision setting a tune can move; re-measure it when the cap feed changes.
KING_FOLD_SHARE = 0.425


# Every saved online game of ours chosen blind to its result (sample_games), each once: every version's online-replays
# index, then online.json's current games (the later record of a game wins).
def saved_games(state: Path) -> list[dict]:
    games: dict = {}
    indexes = [p for p in (state / "online-replays").glob("*/index.json") if p.parent.name.isdigit()]
    for path in sorted(indexes, key=lambda p: int(p.parent.name)):
        games.update((g["id"], g) for g in read(path, {"games": []})["games"])
    games.update((g["id"], g) for g in read(state / "online.json", {}).get("games", []))
    return sample_games(list(games.values()))


# Our side of one game that went to the round limit, for round500: its result and map size band (meta.AREA_BANDS), our
# dragons alive, longest and total at the end, their longest, the fold-all bound (our longest plus half the rest of our
# length: about what folding every other dragon into the king could reach, a death dropping a pearl on every second
# segment), whether we ended with `floor` (P.mergeMinUnits) dragons or fewer, and each side's king at the fold
# (tools/replays.py king_at_fold, None in a summary from before it). None for a game that ended earlier or whose
# summary lacks these numbers.
def round500_game(game: dict, floor: int | None, share: float | None = None) -> dict | None:
    summary = game.get("summary") or {}
    teams, board = summary.get("teams", {}), summary.get("map", {})
    ours = teams.get(game["side"], {}).get("end")
    theirs = teams.get("B" if game["side"] == "A" else "A", {}).get("end")
    if summary.get("result", {}).get("endReason") != "roundLimit" or not ours or not theirs or not board.get("width"):
        return None
    winner, tiles = summary["result"].get("winner"), board["width"] * board["height"]
    return {"id": game["id"], "submission_id": game["submission_id"], "opponent": game.get("opponent"),
            "map_id": game.get("map_id"), "tiles": tiles, "size": meta.area_band(tiles), "side": game["side"],
            "result": "draw" if winner is None else "won" if winner == game["side"] else "lost",
            "requested_at": game.get("requested_at"), "alive": ours["alive"], "longest": ours["longest"],
            "total": ours["total"], "their_longest": theirs["longest"],
            "bound": ours["longest"] + (ours["total"] - ours["longest"]) / 2,
            "bound_share": None if share is None else round(ours["longest"] + share * (ours["total"] - ours["longest"]) / 2, 2),
            "at_floor": None if floor is None else ours["alive"] <= floor,
            "king": teams[game["side"]].get("king"),
            "their_king": teams["B" if game["side"] == "A" else "A"].get("king")}


# Counts over round500_game rows: losses, wins and draws; the losses whose fold-all bound was above their longest (a
# full fold would have out-lengthed them); the losses' mean numbers; with a floor, the losses and wins that ended at
# it, with `floor_p`, the one-sided chance (chance.rates_p) that losses end there this much more often than wins by
# luck alone; and `king` (review M52), for losses and wins apart over the games whose summaries have it, each side's
# mean king at the fold (round500_kings).
def round500_counts(rows: list[dict]) -> dict:
    losses, wins = [r for r in rows if r["result"] == "lost"], [r for r in rows if r["result"] == "won"]
    out: dict = {"losses": len(losses), "wins": len(wins), "draws": len(rows) - len(losses) - len(wins),
                 "bound_above_theirs": sum(r["bound"] > r["their_longest"] for r in losses)}
    if rows and rows[0].get("bound_share") is not None:  # M74: the bound at the king's measured share of a drop
        out["bound_share_above_theirs"] = sum(r["bound_share"] > r["their_longest"] for r in losses)
    if losses:
        out["loss_means"] = {k: round(sum(r[k] for r in losses) / len(losses), 1)
                             for k in ("alive", "longest", "total", "their_longest", "bound", "bound_share")
                             if all(r.get(k) is not None for r in losses)}
    if rows and rows[0]["at_floor"] is not None:
        at_loss, at_win = sum(r["at_floor"] for r in losses), sum(r["at_floor"] for r in wins)
        out |= {"losses_at_floor": at_loss, "wins_at_floor": at_win,
                "floor_p": round(rates_p(at_loss, len(losses), at_win, len(wins)), 3)}
    if kings := {name: k for name, group in (("lost", losses), ("won", wins)) if (k := round500_kings(group))}:
        out["king"] = kings
    return out


KING_PARTS = ("length", "meals", "splitOff", "sprints", "handedOn")
BUILD_PARTS = ("length", "spawned", "teammateDrops", "enemyDrops", "splitOff", "sprints", "handedOn")


# Each side's mean king at the fold (tools/replays.py king_at_fold: its length at the end of r396 and, from r397 on, its
# meals, the pieces it split off, its sprint steps and what a parent kept handing it on to a shorter child) over the
# `rows` whose summaries have both, with n; `their_longer` counts the games their king was longer at r396 and
# `longer_p` is the sign test's chance (chance.sign_p) of that many of the unequal ones by luck alone; and `build`
# (method M99), over the rows whose kings have it, with its own n: its length at the end of r250 and, over r251-396,
# its meals by source (spawned, teammate drops, enemy drops) and the same losses. None when no row has them.
def round500_kings(rows: list[dict]) -> dict | None:
    rows = [r for r in rows if r.get("king") and r.get("their_king")]
    if not rows:
        return None

    def mean(key: str, part: str) -> float:
        return round(sum(r[key][part] for r in rows) / len(rows), 1)

    longer = sum(r["their_king"]["length"] > r["king"]["length"] for r in rows)
    shorter = sum(r["their_king"]["length"] < r["king"]["length"] for r in rows)
    built = [r for r in rows if r["king"].get("build") and r["their_king"].get("build")]

    def build_mean(key: str, part: str) -> float:
        return round(sum(r[key]["build"][part] for r in built) / len(built), 1)

    return {"n": len(rows), "round": rows[0]["king"]["round"],
            "ours": {part: mean("king", part) for part in KING_PARTS},
            "theirs": {part: mean("their_king", part) for part in KING_PARTS},
            "their_longer": longer, "longer_p": float(f"{sign_p(longer, shorter):.2g}"),
            **({"build": {"n": len(built), "round": built[0]["king"]["build"]["round"],
                          "ours": {part: build_mean("king", part) for part in BUILD_PARTS},
                          "theirs": {part: build_mean("their_king", part) for part in BUILD_PARTS}}}
               if built else {})}


# online.md's round-500 section (review M41: nothing reported what decides our online round-500 losses, so the fold
# floor went unseen for 17 promotions), over ladder and autoscrim games (round500_text leaves dev games out): per
# version, the latest ROUND500_VERSIONS with round-limit games, round500_counts overall and per map size, and the
# ROUND500_ROWS most recent of their losses game by game. `floor` is the champion's P.mergeMinUnits; an older version
# may have used another. `games` come from saved_games, which leaves out replays kept because we lost (MISSES 55): they
# would tilt every count and mean that sets losses against wins.
def round500(games: list[dict], floor: int | None, share: float | None = None) -> dict:
    rows = [r for g in games if (r := round500_game(g, floor, share))]
    versions = sorted({r["submission_id"] for r in rows})[-ROUND500_VERSIONS:]
    report = []
    for version in versions:
        mine = [r for r in rows if r["submission_id"] == version]
        sizes = {band: round500_counts([r for r in mine if r["size"] == band])
                 for band, _, _ in meta.AREA_BANDS if any(r["size"] == band for r in mine)}
        report.append({"submission_id": version, **round500_counts(mine), "by_size": sizes})
    losses = sorted((r for r in rows if r["result"] == "lost" and r["submission_id"] in versions),
                    key=lambda r: (r["requested_at"] or "", r["id"]))[-ROUND500_ROWS:]
    return {"floor": floor, "versions": report, "recent_losses": losses[::-1]}


# The saved games round500 reads: every one of ours but dev games.
def round500_games(state: Path) -> list[dict]:
    dev = dev_ids(read(state / "online.json", {}))
    return [g for g in saved_games(state) if not against_dev(g, dev)]


# The versions round500 reports, oldest first, so their summaries are refreshed (refresh_summaries) before others.
def round500_versions(state: Path) -> list[int]:
    return [v["submission_id"] for v in round500(round500_games(state), None)["versions"]]


def round500_text(state: Path, params: str) -> str:
    floor, share = merge_floor(params), KING_FOLD_SHARE
    section = round500(round500_games(state), floor, share)
    share_note = (f"`bound_share` is that bound at the king's measured share of a folder's drop ({share} of ceil(L/2), "
                  "measured on 1,702 feeds of 307dd8c's rule counts, method M74: the king eats about a fifth of a "
                  "folder's length, not half), our longest plus that share of half the rest, and "
                  "`bound_share_above_theirs` counts losses where it was above their longest. ")
    floor_note = (f"`losses_at_floor`/`wins_at_floor` count games we ended with {floor} dragons or fewer (the champion's "
                  f"P.mergeMinUnits; an older version may have used another), and `floor_p` is the one-sided chance that "
                  "losses end there this much more often than wins by luck alone (loop/chance.py rates_p). "
                  if floor is not None else "The champion's params.hpp has no mergeMinUnits, so no floor counts. ")
    return ("round500 (review M41): our online games that went to the round limit, dev games and replays kept only "
            f"because we lost left out, per version (the latest {ROUND500_VERSIONS}), overall and by map size in "
            "tiles. `bound` is the fold-all bound, our longest plus half the rest of our length; `bound_above_theirs` "
            "counts losses where it was above their longest (a full fold with the king eating every drop pearl). "
            + share_note + floor_note + "`king` (review M52), for losses and wins apart: each side's mean king line (the longest "
            "at the end and the dragons it split from) at the end of its `round`, its `length` then and, from the "
            "next round to the end, its `meals` (pearls eaten), `splitOff` (the pieces it split off), `sprints` (paid "
            "sprint steps) and `handedOn` (what a parent kept when it went on in a shorter child), which add up to "
            "its length at the end; `their_longer` counts the games their king was longer then and `longer_p` is the "
            "sign test's chance of that by luck alone; `n` is the games whose summaries have it (older ones are "
            "re-read from their replays a few a cycle). `king.build` (method M99: the r396 gap is built before it) is "
            "the same line from the end of its `round` (r250) to r396: its `length` then, its meals by source "
            "(`spawned`, `teammateDrops`, `enemyDrops`) and `splitOff`, `sprints`, `handedOn`, which add up to its r396 "
            "length, with its own n. Counts per version are small: give each its n.\n"
            + "\n".join(json.dumps(v) for v in section["versions"])
            + f"\nround500 losses (the {ROUND500_ROWS} most recent, newest first):\n"
            + "\n".join(json.dumps(r) for r in section["recent_losses"]) + "\n")


# match-requests.json keeps this many days of rows (at most about 120 dev rows a day at dev_pace, and some 120 others),
# a bound
# on its size: what reads it looks back an hour (remaining_games, pending_teams), six (dev_timing) or at this version's
# rows (requested_games, choose_match), and a version live longer than this has long since played every matchup.
REQUEST_ROWS_DAYS = 2


def recent_rows(rows: list[dict], now: float) -> list[dict]:
    return [r for r in rows if r.get("at", now) > now - REQUEST_ROWS_DAYS * 86400]


# Reserves a request's games in `rows` (match-requests.json) before sending it, then records how it went: "requested"
# with the game ids, "unknown" when the outcome is unclear (it counts as played and holds the team for an hour), or
# "rejected" when the server refused it (no games were queued: it keeps its budget and hour, then may be retried; for
# the dev benchmark a line in host-alerts.log), re-raising the error. A 409 (the site's "You already have a pending
# match against this team", 2026-09-27) means a battle is still pending: its reservation is dropped and it returns
# "pending", neither a failure nor a spent request. A 429 (the API's rate limit) queued nothing either: its reservation
# is dropped and it returns "limited", holding neither the budget nor the team (ladder waits out its Retry-After).
# Dev requests are marked "dev", counted in their own hourly pool (remaining_dev_games); `weights` go into the row
# (a sweep's "share", sweep_shares). Rows older than REQUEST_ROWS_DAYS are dropped as it saves.
def send(state: Path, rows: list[dict], request: dict, sid: int, dev: bool, bench: dict | None,
         probe: str | None = None, weights: dict | None = None) -> str:
    budget_path = state / "match-requests.json"
    reservation = {"at": time.time(), "games": len(request["mapIds"]), "ranked": False, "opponent": request["teamId"],
                   "submission_id": sid, "status": "unknown", "map_ids": request["mapIds"],
                   **({"dev": True} if dev else {}), **({"probe": probe} if probe else {}), **(weights or {})}
    rows[:] = recent_rows(rows, reservation["at"])
    rows.append(reservation)
    save(budget_path, rows)
    try:
        response = ladder.post("/battles", request)
    except urllib.error.HTTPError as refused:
        if refused.code in (409, 429):
            rows.remove(reservation)
            save(budget_path, rows)
            return "pending" if refused.code == 409 else "limited"
        reservation.update(status="rejected", http_status=refused.code)
        save(budget_path, rows)
        if bench and request["teamId"] == bench["id"]:
            with (state / "host-alerts.log").open("a") as handle:
                handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} online: the server refused a dev benchmark battle "
                             f"against team {bench['id']} ({bench['name']}) with HTTP {refused.code}; it is retried "
                             "after an hour\n")
        raise
    reservation.update(status="requested", ids=response.get("ids", []))
    save(budget_path, rows)
    return "requested"


# Teams we can't send a battle now: those with a game still playing in `details` (battle id -> detail; `battles`
# gives each battle's opponent), those requested since the list was read (`listed`: a dev poll may have sent one while
# a cycle collected), and those whose request in the last hour had an unclear outcome or was refused.
def pending_teams(battles: list[dict], details: dict[int, dict], rows: list[dict], listed: float) -> set[int]:
    teams = {b["opponentId"] for b in battles
             if any(g.get("status") in PLAYING for g in details.get(b["id"], {}).get("games", []))}
    return teams | {r["opponent"] for r in rows if r["at"] >= listed and r.get("status") == "requested"} | {
        r["opponent"] for r in rows if r["at"] > listed - 3600 and r.get("status") in ("unknown", "rejected")}


# When a dev battle against `team` was last requested in `rows` (match-requests.json: REQUEST_ROWS_DAYS of rows) by
# any version of ours, refused ones left out; 0 when none.
def last_dev_request(rows: list[dict], team: int) -> float:
    return max((r.get("at", 0) for r in rows if r.get("dev") and r.get("opponent") == team
                and r.get("status") != "rejected"), default=0)


# Whether the latest dev request in `rows` the site didn't refuse went as a probe (its row's `probe`, dev_round's;
# a refused one queued no game): dev_round then sends no
# probe, so probes due together alternate with the share order instead of taking the rounds in a row. Six teams we
# always beat were last requested between 21:01 and 21:18 on 2026-09-27, so each day from about 21:01 they took six
# rounds running, about 72 minutes with no dev battle for the teams that inform.
def after_probe(rows: list[dict]) -> bool:
    last = max((r for r in rows if r.get("dev") and r.get("status") != "rejected"), key=lambda r: r.get("at", 0),
               default=None)
    return bool(last and last.get("probe"))


# Whether dev team `team` (a row of dev_report's) is due its one extra probe of version `sid`: our pooled 95% lower
# bound against it (`lower`) is at least DEV_HEAVY, this version lost a game to it (its `records`), and no "loss" probe
# of this version (`sid` or the rest of its play group, `group`) went to it (`rows`; a refused one queued no game).
# One loss moves the pooled share (dev_weight) of a team we always beat very little, so a regression against it would
# otherwise wait for its next DEV_PROBE_S probe.
def loss_probe_due(team: dict, sid: int, rows: list[dict], group: list[int] = ()) -> bool:
    lost = any(r.get("submission_id") == sid and r.get("losses") for r in team.get("records", []))
    probed = any(r.get("dev") and r.get("opponent") == team["id"] and r.get("submission_id") in {sid, *group}
                 and r.get("probe") == "loss" and r.get("status") != "rejected" for r in rows)
    return (team.get("lower") or 0) >= DEV_HEAVY and lost and not probed


# One round of the dev battles, inside the requests' lock: marks finished dev battles in `rows` (mark_done, as of
# `listed`, when `battles` and `details` were read), then, once a dev battle is due (dev_due) and unless a stop marker
# is set, tries dev_requests' battles for `dev` (online.json's "dev": the teams with their shares and the active maps)
# in order until one reserves its games: a 409 (still pending) tries the next team, a 429 stops the round, and any
# other outcome, a refused or unclear one too, is the round's one battle. `pending` is from pending_teams.
# A team due a probe goes first (dev_requests' `probes`), unless the latest dev request the site didn't refuse was a probe (after_probe): one
# with no dev battle requested for DEV_PROBE_S (last_dev_request; its row gets "probe": "day"), which shows whether
# the dev team's bot changed and is how a team we always beat is played at all, or one due its extra probe after a
# loss this version (loss_probe_due; "probe": "loss"). Returns the round for online.json and dev-poll.json: {at,
# ahead, queued, sent, error, tried, pending, probes, held, allowance, due}, `queued` ours waiting behind others'
# (waiting), `pending` the teams that answered 409 (a battle still pending that `pending` didn't show; dev_poll's
# `stuck` follows them), `probes` the teams due a probe, `held` whether they waited this round for a share pick,
# `allowance` the dev games left this rolling hour after the round (remaining_dev_games) and `due` when the next dev
# battle may go (dev_due).
def dev_round(state: Path, stop_path: Path, sid: int, dev: dict, battles: list[dict], details: dict[int, dict],
              rows: list[dict], pending: set[int], bench: dict | None, listed: float) -> dict:
    now = time.time()
    if mark_done(rows, list(details.values()), listed):
        save(state / "match-requests.json", rows)
    teams = dev.get("teams", [])
    queued = [b for b in battles if b.get("wait") and b.get("opponentId") in {t["id"] for t in teams}]
    last = {t["id"]: last_dev_request(rows, t["id"]) for t in teams}
    group = dev.get("play_group") or [sid]  # the cycle's play_group
    kinds = {t["id"]: "day" if now - last[t["id"]] >= DEV_PROBE_S else "loss" for t in teams
             if now - last[t["id"]] >= DEV_PROBE_S or loss_probe_due(t, sid, rows, group)}
    probes, held = {team: last[team] for team in kinds}, after_probe(rows)
    maps = dev.get("maps", [])
    requests = dev_requests(teams, {t["id"]: t["weight"] for t in teams}, requested_games(rows, sid, group), queued,
                            pending, maps, {} if held else probes)
    if stop_path.exists() or (state / "ASTRA_STOP").exists() or now < dev_due(rows, maps):
        requests = []
    sent, tried, still, error = [], [], [], None
    for request in requests:
        try:
            outcome = send(state, rows, request, sid, True, bench, None if held else kinds.get(request["teamId"]))
        except Exception as failure:  # refused or unclear: its games are spent, so it is this round's battle
            error = error or f"{type(failure).__name__}: {failure}"[:300]
            tried.append(request["teamId"])
            break
        if outcome == "limited":  # the API's rate limit: the rest wait for the next round
            error = error or "HTTP 429: rate limited, the rest wait for the next round"
            break
        tried.append(request["teamId"])
        if outcome == "requested":
            sent.append(request["teamId"])
            break
        still.append(request["teamId"])
    return {"at": now, "ahead": queue_ahead(queued), "queued": sum(map(waiting, queued)), "sent": sent, "error": error,
            "tried": tried, "pending": still, "probes": sorted(probes), "held": held,
            "allowance": remaining_dev_games(rows, time.time()), "due": dev_due(rows, maps)}


# dev_poll's record of dev teams answering 409 to every try, {team id: {"since", "last"}}: a team that answered 409
# this round (`round_`, dev_round's) keeps its first 409 time and gets this one as its last, and a team tried with
# any other outcome leaves. A battle that answers 409 though pending_teams saw nothing of it playing in the poll's
# window is stuck on the site, and blocks that dev team for good; health.dev_battles alerts past its DEV_STUCK.
def stuck_teams(stuck: dict[str, dict], round_: dict, now: float) -> dict[str, dict]:
    out = {team: times for team, times in stuck.items() if int(team) not in round_.get("tried", [])}
    for team in round_.get("pending", []):
        out[str(team)] = {"since": stuck.get(str(team), {}).get("since", now), "last": now}
    return out


# While this marker is in the loop home (sig plays the games himself), the loop requests no online battles, dev or
# not; our finished games are still collected. Written and removed by hand.
PAUSED = "ONLINE_PAUSED"
DEV_POLL_EVERY = 30  # dev_poll's period: a finished dev battle is followed within about this long
POLL_WINDOW = 20  # the newest battles dev_poll reads (dev battles in flight are at most one per dev team)
# dev_poll sends only on a dev team list (and active maps) the cycle read within this long: past it the team list may
# be stale (a dev flag cleared, so the site counts that team's games in the other teams' hourly 60 while we don't, or a map left
# the pool, so every request is refused), and a cycle that can't run or keeps failing stops dev battles within it.
DEV_FRESH = 300


# Whether the loop may play online matches, from publishing's record (publishing.json, or its cycle's return): not
# while a build is blocked, waiting to activate or in error, when the live bot may not be the verified champion. The
# upkeep tick's gate for online.cycle and dev_poll's.
def matches_allowed(published: dict) -> bool:
    return not any(published.get(k) for k in ("blocked", "pending", "error"))


# Why dev_poll sends nothing now, or None when it may: a 429's Retry-After running (ladder.quiet), publishing barring
# matches (matches_allowed, from publishing.json), or online.json (`report`) naming no dev teams for its live
# submission, or naming them from a read older than DEV_FRESH, or no dev battle due yet by `rows`
# (match-requests.json, dev_due): "paced: ..." or "dev allowance spent: ...", with the time it is due.
def poll_skip(published: dict, report: dict, now: float, rows: list[dict] = ()) -> str | None:
    if ladder.quiet():
        until = time.strftime("%H:%M:%S", time.localtime(ladder.quiet_until()))
        return f"429 wait: the API's Retry-After runs to {until}"
    if not matches_allowed(published):
        return "matches not allowed: publishing.json is " + ", ".join(
            k for k in ("blocked", "pending", "error") if published.get(k))
    dev, sid = report.get("dev") or {}, report.get("submission_id")
    if not sid:
        return "no live submission in online.json"
    if dev.get("submission_id") != sid or not dev.get("teams"):
        return f"no dev teams in online.json for submission {sid}"
    at = dev.get("at")
    if not isinstance(at, (int, float)) or now - at > DEV_FRESH:
        read_at = f"{(now - at) / 60:.0f} min ago" if isinstance(at, (int, float)) else "at no recorded time"
        return (f"stale dev list: online.json's dev teams were read {read_at}, over DEV_FRESH ({DEV_FRESH // 60} "
                "min); the cycle can't run or keeps failing")
    maps = dev.get("maps") or []
    if now >= (due := dev_due(list(rows), maps)):
        return None
    at = time.strftime("%H:%M:%S", time.localtime(due))
    left = remaining_dev_games(list(rows), now)
    if left < len(maps):
        return (f"dev allowance spent: {left} of DEV_GAMES_AN_HOUR ({DEV_GAMES_AN_HOUR}) dev games left this rolling "
                f"hour, a battle needs {len(maps)}; the next is due at {at}")
    return f"paced: one dev battle every {dev_pace(maps):.0f} s (dev_pace); the next is due at {at}"


def dev_poll(state: Path, stop_path: Path | None = None) -> dict | None:
    """Between cycles, requests the next dev battle soon after the last one ends (a battle of every ladder map took
    113 s on 2026-09-27, while cycles are DEV_EVERY apart and wait on the upkeep tick): reads the battle list and the
    details of dev battles not yet seen finished, then runs dev_round with the dev teams, shares and maps the last
    cycle wrote to online.json. Nothing within DEV_POLL_EVERY of the last poll by any process (checked first, before
    online.json is parsed), nor while publishing bars matches (matches_allowed) or a 429's Retry-After runs
    (ladder.quiet), or unless online.json has dev teams for its live submission read within DEV_FRESH, or before the
    next dev battle is due (dev_due: its pace and the hour's allowance), so a poll reads the API only when it may
    send; such a poll records why in dev-poll.json's `skipped` (poll_skip), which health.dev_battles' stall alert
    quotes. It takes the
    requests' lock, which a cycle holds only while it requests, so a cycle's minute of collection never holds it up.
    Its record, dev-poll.json, holds the last round and the battle ids seen finished (at most POLL_WINDOW): up to
    about ten API calls a poll."""
    stop_path = stop_path or state / "STOP"
    if (state / PAUSED).exists():
        return None
    with locked(state / "match-requests"):
        record_path = state / "dev-poll.json"
        record = read(record_path, {})
        now = time.time()
        if now - record.get("at", 0) < DEV_POLL_EVERY:
            return None
        record["at"] = now  # a look counts as a poll, so online.json is parsed at most every DEV_POLL_EVERY
        report = read(state / "online.json", {})
        rows = read(state / "match-requests.json", [])
        record["skipped"] = poll_skip(read(state / "publishing.json", {}), report, now, rows)
        save(record_path, record)
        if record["skipped"]:
            return None
        dev, sid = report["dev"], report["submission_id"]
        try:
            ids = {t["id"] for t in dev["teams"]}
            battles = ladder.items(ladder.get(f"/battles?limit={POLL_WINDOW}"), "battles")
            finished, details = set(record.get("finished", [])), {}
            for battle in battles:
                if battle.get("opponentId") in ids and battle["id"] not in finished:
                    time.sleep(0.6)
                    details[battle["id"]] = ladder.get(f"/battles/{battle['id']}")
            round_ = dev_round(state, stop_path, sid, dev, battles, details, rows,
                               pending_teams(battles, details, rows, now), (report.get("benchmark") or {}).get("team"),
                               now)
            finished |= {b for b, d in details.items() if not any(g.get("status") in PLAYING for g in d.get("games", []))}
            record = {**round_, "finished": [b["id"] for b in battles if b["id"] in finished],
                      "stuck": stuck_teams(record.get("stuck", {}), round_, now)}
        except (Exception, SystemExit) as error:
            record["error"] = f"{type(error).__name__}: {error}"[:500]
        save(record_path, record)
        return record


# Runs dev_poll a few times a DEV_POLL_EVERY in a background thread while the block runs (maintenance.checking), until
# `stopped()`: the upkeep tick can take many minutes, and a dev battle about two. A failing poll is printed and the
# next one still runs.
@contextlib.contextmanager
def polling(state: Path, stop_path: Path, stopped: Callable[[], bool] = lambda: False) -> Iterator[None]:
    done = threading.Event()

    def loop() -> None:
        while not done.is_set() and not stopped():
            try:
                dev_poll(state, stop_path)
            except Exception as error:
                print(f"dev poll failed: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
            done.wait(DEV_POLL_EVERY / 3)

    import maintenance
    thread = threading.Thread(target=loop, name="dev-poll", daemon=True)
    thread.start()
    try:
        with maintenance.exec_part("dev-poll", done, thread):  # settled before an exec (before_exec)
            yield
    finally:
        done.set()
        thread.join(timeout=10)


def cycle(repo: Path, state: Path, stop_path: Path | None = None) -> dict:
    """One pass of the online games: collect our finished games (collect_games) from the listed battles and those
    carried past the list (owed_battles, requested_battles), write the reports, then request battles, reserving each
    request's games before sending it (send). A non-dev battle (choose_match) at most every
    REQUEST_EVERY, within the hourly cap; dev battles (dev_round, and dev_poll between cycles) one at a time as each is
    due (dev_due), within their own hourly allowance, to a dev team with none pending while the queue is short: one
    unplayed for DEV_PROBE_S first, else by share (dev_requests).
    Runs every DEV_EVERY while dev teams are listed, so their games are collected as they come, else every
    REQUEST_EVERY."""
    stop_path = stop_path or state / "STOP"
    if (state / PAUSED).exists():  # collection goes on; requests and dev battles read it as their stop marker
        stop_path = state / PAUSED
    with locked(state / "online"):
        path = state / "online.json"
        report = read(path, {})
        now = time.time()
        if now - report.get("attempt_at", 0) < (DEV_EVERY if (report.get("dev") or {}).get("teams") else REQUEST_EVERY):
            return report
        report["attempt_at"] = now
        save(path, report)
        try:
            active = active_submission()
            if not active:
                raise RuntimeError("no active submission")
            sid = active["id"]
            group = play_group(state, sid)  # the submissions that play as sid: their games are sid's
            # Drop the prior version's current report before any fallible API/replay work, keeping when collection
            # last succeeded (loop/health.py alerts on its age).
            # The benchmark spans versions, so it's kept for benchmark_news, and opponent_versions for version_news;
            # `dev`, `settled`, `behind` and `closed` for the next cycle; `layouts` for layout_news, `held` for prune
            # (hold_replays) and `compare_zero_cycles` for the count.
            report = {"attempt_at": now, "submission_id": sid, "play_group": group,
                      "games": current_games(report, sid),
                      **{k: report[k] for k in ("fetched_at", "benchmark", "dev", "settled", "behind", "closed",
                                                "rotation_at", "margin_rechecks", "margin_recheck_error",
                                                "opponent_versions", "layouts", "held", "api_missing", "bceval_error",
                                                "compare_zero_cycles")
                         if k in report}}
            save(path, report)
            team = ladder.get("/team")["team"]
            teams = ladder.items(ladder.get("/leaderboard"), "teams")
            dev = dev_opponents(teams, team["id"])
            maps = ladder.items(ladder.get("/maps"), "maps")
            bench = dev_benchmark(teams)
            # Games against these are dev games: every dev team with a bot, and the benchmark.
            dev_set = {t["id"] for t in dev} | ({bench["id"]} if bench else set())
            listed = time.time()
            battles = ladder.items(ladder.get(f"/battles?limit={BATTLE_WINDOW}"), "battles")
            settled, details = set(report.get("settled", [])), {}
            # Then up to CARRIED_PER_CYCLE battles past the list whose games aren't all recorded, oldest first
            # (owed_battles, requested_battles), none closed.
            listed_ids = [b["id"] for b in battles]
            behind, closed = report.get("behind") or {}, report.get("closed") or {}
            stamps = {**requested_battles(state, recorded_ids(state), now), **{int(b): t for b, t in behind.items()}}
            carried = sorted((b for b in stamps if b not in listed_ids and str(b) not in closed),
                             key=lambda b: (stamps[b], b))[:CARRIED_PER_CYCLE]
            settled |= {int(b) for b in closed}
            for battle_id in [b for b in listed_ids if b not in settled] + carried:
                if ladder.quiet():  # a 429's Retry-After: the rest are read next cycle (collect_games too)
                    break
                time.sleep(0.6)
                try:
                    details[battle_id] = ladder.get(f"/battles/{battle_id}")
                except Exception as error:
                    if isinstance(error, urllib.error.HTTPError) and ladder.limited(error):
                        break
                    if battle_id not in carried:
                        raise
                    # A carried battle that can't be read (an HTTP error, a timeout, bad JSON) stays carried
                    # (owed_battles) and is tried next cycle.
            refresh_summaries(repo, state, list(dict.fromkeys(  # the live version first: its readers are the newest
                [*group, *filter(None, [previous_version(state, sid)[0]]), *round500_versions(state)])))
            refresh_dooms(repo, state, sid)
            refresh_rams(state, sid, dev_set)
            refresh_starts(state, [*group, *filter(None, [previous_version(state, sid)[0]])])
            report["api_missing"], news = api_news(report.get("api_missing"), missing_fields(list(details.values())),
                                                   time.time())
            for line in news:
                meta.alert(state, line)
            games = collect_games(repo, state, team["id"], sid, list(details.values()), dev_set, map_ids_by_name(maps))
            # An identical build's saved games are this version's too, older first (readers take the newest last).
            games = group_index(state, group[1:][::-1])["games"] + games
            try:  # after collection, so its minute never delays the games; alerted once per distinct error
                fresh = refresh_bceval(state, sid)
                for game in games:
                    if game.get("id") in fresh:
                        game["bceval"] = fresh[game["id"]]
                report.pop("bceval_error", None)
            except Exception as error:
                message = f"{type(error).__name__}: {error}"[:300]
                if report.get("bceval_error") != message:
                    meta.alert(state, f"online: refresh_bceval failed, retried each cycle: {message}")
                report["bceval_error"] = message
            recorded = recorded_ids(state)
            report["unrecorded"] = unrecorded_games(battles, details, recorded)
            report["settled"] = settled_battles(battles, details, settled, recorded)
            report["behind"], report["closed"], news = owed_battles(details, recorded, behind, closed, time.time())
            for line in news:
                meta.alert(state, line)
            previous_sid, previous = previous_version(state, sid)
            report.update(games=games, per_map=per_map_stats([g for g in games if not against_dev(g, dev_set)]),
                          fetched_at=time.time(),
                          compare=ladder_compare(previous_sid, previous, games, dev_set,
                                                 refresh_splits(state, time.time())))
            # Cycles in a row whose compare had games to pair that a rating read has judged, and paired none
            # (health.online_compare, REFLECT F14).
            compare = report["compare"] or {}
            idle = compare.get("candidates", 0) > compare.get("unread", 0) and not compare.get("pairs")
            report["compare_zero_cycles"] = report.get("compare_zero_cycles", 0) + 1 if idle else 0
            if held := hold_replays(report.get("held"), report["compare"], sid, time.time()):
                report["held"] = held
            else:
                report.pop("held", None)
            report["layouts"], news = layout_news(report.get("layouts"), games)
            for line in news:
                meta.alert(state, line)
            # Its own failure never stops the games: it is alerted once per distinct error and retried next cycle.
            try:
                raise_rechecks(repo, state, report)
                report.pop("margin_recheck_error", None)
            except Exception as error:
                message = f"{type(error).__name__}: {error}"[:500]
                if report.get("margin_recheck_error") != message:
                    meta.alert(state, f"online: raise_rechecks (review M33) failed, retried each cycle: {message}")
                report["margin_recheck_error"] = message
            # Every index read once for both reports and the watched teams' versions.
            top = [t["id"] for t in sorted(teams, key=lambda t: t.get("rank") or 10**9)
                   if not is_dev(t) and t["id"] != team["id"]][:WATCHED_TOP]
            played = played_against(state, dev_set | set(top), sid, games)
            versions = opponent_versions(played)
            others = {k: v for k, v in versions.items() if not bench or k != str(bench["id"])}  # benchmark_news's
            for line in version_news(report.get("opponent_versions") or {}, others, teams):
                meta.alert(state, line)
            report["opponent_versions"] = {**(report.get("opponent_versions") or {}), **versions}
            benchmark = benchmark_report(state, bench, sid, games, played) if bench else None
            if news := benchmark and benchmark_news(report.get("benchmark"), benchmark):
                with (state / "host-alerts.log").open("a") as handle:
                    handle.writelines(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n" for line in news)
            # Without a benchmark this cycle, keep the last one, so its return isn't news again.
            report["benchmark"] = benchmark or report.get("benchmark")
            report["local_sides"] = local_sides(state, sid)  # beside dev map rows, all one side (review M48)
            last = report.get("dev") or {}
            standing = dev_report(state, dev, sid, games, played)
            with locked(state / "match-requests"):  # the requests' own lock, which dev_poll takes
                rows = read(state / "match-requests.json", [])
                pending = pending_teams(battles, details, rows, listed)
                report["dev"] = {"submission_id": sid, "play_group": group, "at": listed,
                                 "since": last.get("since", now) if last.get("submission_id") == sid else now,
                                 "teams": standing, "maps": sorted(m["id"] for m in maps if m.get("active")),
                                 "timing": dev_timing(rows, now), "winner_check": winner_check(games, dev_set)}
                request, shares = None, {}
                if now - report.get("rotation_at", 0) >= REQUEST_EVERY:
                    scores = sweep_scores(state, games, dev_set, now)
                    request = choose_match(teams, maps, team["id"], team["elo"], pending, rows, sid, now, games,
                                           previous, scores, group, sweep_targets(state, now))
                    shares = sweep_shares(teams, team["id"], team["elo"], scores)
                if (active_submission() or {}).get("id") != sid:
                    report.update(games=[], per_map=[], submission_id=None, play_group=None)
                    raise RuntimeError("active submission changed during collection; retry next cycle")
                report["dev"].update(dev_round(state, stop_path, sid, report["dev"], battles, details, rows, pending,
                                               bench, listed))
                if request and (stop_path.exists() or (state / "ASTRA_STOP").exists()):
                    request = None
                if request:
                    rotation_at, report["rotation_at"] = report.get("rotation_at", 0), now
                    weights = {"share": round(shares.get(request["teamId"], 0), 3)}  # health.sweep_shares
                    if send(state, rows, request, sid, False, bench, weights=weights) == "limited":  # ask next cycle
                        report["rotation_at"] = rotation_at
                    else:
                        report["requested"] = request
            report["error"] = None
        except (Exception, SystemExit) as error:
            report["error"] = f"{type(error).__name__}: {error}"[:1200]
        save(path, report)
        return report
