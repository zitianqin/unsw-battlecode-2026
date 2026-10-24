"""Riser study (sig, 2026-09-28): while a Claude loop's upkeep runs, find the top-TOP teams whose rating rose beyond
chance, fetch their ranked games from before and after each rise, and have a headless worker (loop/RISERS.md) say what
changed in their play and queue up to MAX_IDEAS ideas. Checked every CHECK_EVERY, so a rise is studied at the first check
after it passes over the past WINDOW (the first check: the past FIRST_WINDOW), and a new rank 1 at the first check
after it takes the top; each team studied at most once every EVERY, and a day with
nobody to study says so once ("RISERS none"). Off under a native session (no Claude worker there), like map ideas.
The check's reads and downloads (`prepare`, minutes when a team is studied) run in a background process of their own
(`risers.py prepare`), so the upkeep tick never waits for them.

The trusted side, this module (never the worker):
- `ratings`: one GET /ratings, the site's rating history: a point per settled ranked battle (steps never exceed the
  new-bot K of 96), the last point the read itself at the current rating.
- `pick`: for each of the top TOP ranked teams by rating (dev teams and us left out), its largest climb inside the
  window (the highest point minus the lowest before it) against the same battles' rating changes with each sign
  drawn at random (SAMPLES paths, seeded): a settled bot's gains and losses are symmetric, so how often random signs
  climb as far is the chance of the rise with that team's battle count and K. Passing at p <= chance.TRIGGER_P over
  the teams looked at (Bonferroni), the smallest p first. Before and after split at the climb's low, which is the
  window's start when the climb began before it: the worker gets the rating path from a window earlier to judge that.
- `beats_prediction`: a passing rise is studied only when its after side's ranked battles won more games than the
  ratings then predicted (Elo's expected share from both teams' histories) by more than chance, since a new bot's
  K of 96 turns a small overperformance into a big rise (the one-off study of 2026-09-28 found forgot to mention's
  and Team SKKU's jumps were mostly that); the site names no versions now, so each side is its span of time. One
  that rose on K alone is listed, not studied, and not rechecked as a rise for EVERY (`k_only`). A new rank 1 is
  studied either way, first, so the MAX_TEAMS cap never drops it.
- `team_battles`: the team's battles from the site's battle list (tools/replays.py battles: 1 s between requests,
  at most MAX_PAGES pages), ranked only (Cutlery played an old build unranked on 2026-09-28): before, from WINDOW
  before the low to it, and after, from the low to the peak, each read back from its newest end; since ~19:30 AEST
  2026-09-28 the API names no submissions, so before and after go by time. The newest BATTLES_PER_SIDE ranked
  battles on each side are downloaded (tools/replays.py fetch --battle, the leader study's path) into
  riser-replays/<run>/ and summarised there (tools/replays.py summary), their losses and draws also read by bceval
  (tools/bceval_read.py: where each turned and which group carried the fall, a line a period in context.md).
- The worker's folder risers/<run>/ gets context.md (the pick, each team's records before and after by opponent,
  the replays and per-game means of their side), teams/<id>.json (every summary), backlog.md (meta.waiting_ideas),
  the champion's bot/params.hpp and bot/CLAUDE.md, study.md, playbook.md, briefing.md and, for a team studied
  before (`studied_in`), that run's findings as earlier/<id>.md, told to report only what changed since. One worker a
  run studies every team in it.
  It writes findings.md (R1, R2, ...: loop/decisions.py) and ideas.jsonl; `apply` reads them only through
  meta.worker_file, validates the ideas (meta.valid_proposals) and queues them with source "risers:<run>"
  (meta.append_ideas, which lists them in applied/ for the orchestrator's decisions), then alerts "RISERS applied".
  Every outcome line starts "RISERS" (applied, none, failed, abandoned), the orchestrator's watcher matches it.
  The worker launches as the audit's does; a failed launch is retried, and the run abandoned after meta.MAX_LAUNCHES.

Records and sizes: <loop home>/risers.json (fixed keys, the last check's pick, `seen` one time per team studied and
`k_only` one per rise judged K alone in the last EVERY, `studied_in` each team's last run folder while that is kept,
at most 10 abandoned folders); risers-fetch.log, the last background check's output, rewritten each check; risers/
one folder a run, a few a day at most (context, summaries and findings, about 1 MB), and worker-io/risers/ its prompt
and output, the newest KEEP_FOLDERS runs of each (under 50 MB); riser-replays/ the newest KEEP_RUNS runs' replays
(each at most MAX_TEAMS x 2 x BATTLES_PER_SIDE x 5 games of about 0.7 MB, so under 150 MB). `prune` keeps all three;
health.riser_study checks the first firing, overdue, pending and abandoned runs, and the three folders' bounds.

    .venv/bin/python loop/risers.py pick [--hours 48]   # a read-only dry run: one GET /ratings, prints the pick
    .venv/bin/python loop/risers.py prepare <loop home>   # the background check the tick starts (start_fetch)
    uv run loop/risers.py --studied 306 70   # teams studied by hand today: no check studies them for EVERY
    .venv/bin/python loop/risers.py apply <loop home> <run folder>   # what collect runs in a fresh process
"""

from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import chance
import decoys
import maintenance
import meta
import online
import quota
from discord_teams import OUR_TEAM

REPO = Path(__file__).resolve().parent.parent
PROMPT = REPO / "loop/RISERS.md"
EVERY = 24 * 3600  # sig, 2026-09-28: "make it fire every day for the past 24 hours"; also each team's study gap
FIRST_WINDOW = 48 * 3600  # sig: the first run covers "the past 48 hours"
WINDOW = 24 * 3600
CHECK_EVERY = 8 * 3600  # sig, 2026-09-28 23:33: "the riser study should run every 8 hours, not hourly"; one GET /ratings
TOP = 35  # sig: "just like say the top 35 ish teams"; not a rating band, so it follows the ladder as it moves
SAMPLES = 4000  # sign-drawn paths per team: resolves p to 1/4001, well below TRIGGER_P / TOP (0.0029)
# Bare bounds because each studied team costs about 70 site requests (70 s), 30 replays (20 MB) and worker time:
MAX_TEAMS = 5  # teams studied a run; the others passing are listed in context.md, not fetched
BATTLES_PER_SIDE = 3  # ranked battles (5 games each) downloaded before and after each rise
MAX_PAGES = 30  # battle-list pages (10 battles each) read per team, bisection included
GAMES_PER_BATTLE = 5  # a ranked battle is a five-game series (docs/unsw/api.md)
MAX_CHECKED = 2 * MAX_TEAMS  # rises a check reads battles for: bare bound because each costs up to MAX_PAGES pages
MAX_IDEAS = 5
RETRY_S = 3600  # after a failed fetch or worker (one a quota limit stopped: at its reset, meta.when_due)
KEEP_RUNS = 2  # riser-replays/ runs kept: the pending one and the last
KEEP_FOLDERS = 30  # risers/ and worker-io/risers/ runs kept: about a week's, for the orchestrator's decisions on each
PATH_MARKS = 6  # context.md's rating path: marks a window apart / this, for reading only
# risers.json's keys that outlive a run (the check's clock, who was checked, the first firing): apply and an abandoned
# launch keep them.
CLOCK = ("attempt_at", "checked_at", "leader", "seen", "k_only", "studied_in", "reported_at", "last_pick",
         "first_seen", "fetch_pid", "fetch_at")


def read(state: Path) -> dict:
    return online.read(state / "risers.json", {})


def save(state: Path, record: dict) -> None:
    online.save(state / "risers.json", record)


def moment(text: str) -> float:
    return datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()


def iso(at: float) -> str:
    return datetime.fromtimestamp(at, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def local(at: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(at))


# A check is due when no run is pending, the last check was CHECK_EVERY ago (or none was), and no read failed and
# no check started (`attempt_at`: one that died mid-fetch) in RETRY_S.
def due(record: dict, now: float) -> bool:
    return (not record.get("pending") and now - record.get("checked_at", 0) >= CHECK_EVERY
            and now - record.get("failed_at", 0) >= RETRY_S and now - record.get("attempt_at", 0) >= RETRY_S)


def window_of(record: dict) -> float:
    return WINDOW if "checked_at" in record else FIRST_WINDOW


# Whether team `team` may be looked at `now`: not in record[`key`] in the last EVERY (`seen`: studied; `k_only`: a
# rise judged K alone).
def fresh(record: dict, team: object, now: float, key: str = "seen") -> bool:
    return now - (record.get(key) or {}).get(str(team), 0) >= EVERY


# The largest climb along `elos`: (the highest point minus the lowest before it, the low's index, the high's index).
def climb(elos: list[float]) -> tuple[float, int, int]:
    best, low, span = 0.0, 0, (0, 0)
    for j, elo in enumerate(elos):
        if elo < elos[low]:
            low = j
        if elo - elos[low] > best:
            best, span = elo - elos[low], (low, j)
    return best, *span


def steps_climb(steps: list[float]) -> float:
    best = level = low = 0.0
    for step in steps:
        level += step
        low = min(low, level)
        best = max(best, level - low)
    return best


# The chance that `steps` with each sign drawn at random climbs at least `rise`: (1 + hits) / (1 + draws), stopping
# once it can no longer come under `alpha`, so a team that clearly didn't rise costs a few dozen draws.
def sign_flip_p(steps: list[float], rise: float, alpha: float, samples: int, seed: str) -> float:
    if rise <= 0:
        return 1.0
    rng, sizes, hits = random.Random(seed), [abs(s) for s in steps], 0
    for n in range(1, samples + 1):
        if steps_climb([s if rng.random() < 0.5 else -s for s in sizes]) >= rise:
            hits += 1
            if (1 + hits) / (1 + samples) > alpha:
                return (1 + hits) / (1 + n)
    return (1 + hits) / (1 + samples)


# A team's rating history as [(time, elo)], without the read's own last point (the current rating again).
def points(history: list[dict]) -> list[tuple[float, float]]:
    found = [(moment(p["date"]), float(p["elo"])) for p in history if isinstance(p, dict) and "date" in p
             and isinstance(p.get("elo"), (int, float))]
    return found[:-1] if len(found) > 1 and found[-1][1] == found[-2][1] else found


# A team's rating at time `at` from its history points: the last point's at or before it (1500 before any).
def rating_at(history: list[tuple[float, float]], at: float) -> float:
    before = [e for t, e in history if t <= at]
    return before[-1] if before else (history[0][1] if history else 1500.0)


# Every top-`top` team's rise over the `window` ending at the read (the latest history point), from /ratings'
# payload `board`: rows {id, name, rank, elo, battles, net, climb, low_at, low_elo, peak_at, peak_elo, path, p,
# picked, earlier}, the smallest p first, with the read's time and the p a pick needs. The climb's low is where before
# and after split; `path` is the rating at 2 x PATH_MARKS + 1 even marks from a window before the window to its end,
# so the worker sees a climb that began before the window; `earlier` counts the rated battles in that window before.
def pick(board: dict, window: float, top: int = TOP, samples: int = SAMPLES) -> tuple[list[dict], float, float]:
    histories = board.get("histories") or {}
    ends = [moment(h[-1]["date"]) for h in histories.values() if isinstance(h, list) and h]
    if not ends:
        raise ValueError("/ratings gave no rating history")
    end = max(ends)
    start = end - window
    ladder = [t for t in board.get("ladder") or [] if isinstance(t, dict) and t.get("rank") and not t.get("dev")
              and str(t.get("id")) != OUR_TEAM]
    teams = sorted(ladder, key=lambda t: t["rank"])[:top]
    alpha = chance.TRIGGER_P / max(1, len(teams))
    rows = []
    for team in teams:
        history = points(histories.get(str(team["id"])) or [])
        path = [(start, rating_at(history, start))] + [(t, e) for t, e in history if start < t <= end]
        elos = [e for _, e in path]
        rise, low, high = climb(elos)
        steps = [b - a for a, b in zip(elos, elos[1:])]
        p = sign_flip_p(steps, rise, alpha, samples, f"{team['id']}:{int(end)}")
        marks = [start - window + k * window / PATH_MARKS for k in range(2 * PATH_MARKS + 1)]
        rows.append({"id": team["id"], "name": team.get("name"), "rank": team["rank"], "elo": team.get("elo"),
                     "battles": len(steps), "earlier": sum(start - window < t <= start for t, _ in history),
                     "net": elos[-1] - elos[0], "climb": rise, "low_at": path[low][0],
                     "low_elo": path[low][1], "peak_at": path[high][0], "peak_elo": path[high][1],
                     "path": [(m, rating_at(history, m)) for m in marks], "p": p, "picked": p <= alpha})
    rows.sort(key=lambda r: (r["p"], -r["climb"]))
    return rows, end, alpha


def ratings() -> dict:
    import ladder
    board = ladder.get("/ratings")
    if not isinstance(board, dict):
        raise ValueError("/ratings did not return an object")
    return board


def run_tool(args: list[str], timeout: int = 900) -> str:
    return maintenance.command([sys.executable, str(REPO / "tools/replays.py"), *args], REPO, timeout=timeout)


# A picked team's ranked battles, oldest first, on each side of its low, from the site's battle list: before, from
# WINDOW before the low to it, and after, from the low to the peak, each read from its newest end (the low, the peak)
# with half of MAX_PAGES, so a team with many unranked battles still gets both sides (on 2026-09-28 one 30-page read
# back from Cutlery's peak found only after-side battles: most of its 1,739 listed battles are unranked challenges).
# Returns (before, after, whether both reads covered their whole span). `tool` runs tools/replays.py.
def team_battles(row: dict, tool: Callable[[list[str]], str] = run_tool) -> tuple[list[dict], list[dict], bool]:
    sides, complete = [], True
    for since, until in ((row["low_at"] - WINDOW, row["low_at"]), (row["low_at"], row["peak_at"])):
        listed = json.loads(tool(["battles", "--team", str(row["id"]), "--since", iso(since), "--until", iso(until),
                                  "--max-pages", str(MAX_PAGES // 2)]))
        sides.append([b for b in listed.get("battles", []) if isinstance(b, dict) and b.get("ranked") is True
                      and settled(b)])
        complete = complete and bool(listed.get("complete"))
    before = [b for b in sides[0] if moment(b["at"]) < row["low_at"]]
    after = [b for b in sides[1] if moment(b["at"]) >= row["low_at"]]
    return before, after, complete


# Whether battle row `b` has finished: a live one ("outcome": "live", games "pending") moved no rating yet and has
# games without replays.
def settled(b: dict) -> bool:
    return b.get("outcome") != "live" and "pending" not in (b.get("resultsA") or [])


# The battles to download for a team: the newest BATTLES_PER_SIDE of each side (nearest the change and the peak).
def chosen(before: list[dict], after: list[dict]) -> list[dict]:
    return before[-BATTLES_PER_SIDE:] + after[-BATTLES_PER_SIDE:]


# Our team's side ("a" or "b") of battle row `b` for team `team`, or None.
def side_of(b: dict, team: int) -> str | None:
    return "a" if (b.get("a") or {}).get("id") == team else "b" if (b.get("b") or {}).get("id") == team else None


# A side's record over battle rows: games won, lost and drawn, the rating it gained, and each opponent's (name,
# games won, games played).
def battle_record(battles: list[dict], team: int) -> dict:
    total = {"battles": 0, "won": 0, "games": 0, "rating": 0, "opponents": {}}
    for b in battles:
        side = side_of(b, team)
        if side is None:
            continue
        other = "b" if side == "a" else "a"
        results = [r for r in b.get("resultsA") or [] if r in ("win", "loss", "draw")]
        won = sum(r == ("win" if side == "a" else "loss") for r in results) + 0.5 * results.count("draw")
        total["battles"] += 1
        total["won"] += won
        total["games"] += len(results)
        total["rating"] += b.get(f"eloChange{side.upper()}") or 0
        opponent = b.get(other) or {}
        name, w, g = total["opponents"].get(str(opponent.get("id")), (opponent.get("name"), 0, 0))
        total["opponents"][str(opponent.get("id"))] = (name, w + won, g + len(results))
    return total


# Team `team`'s games in battle rows `battles` as the decoy check reads them (loop/decoys.py): (None, the battle's
# time, its score), oldest first; the battle list names no submissions.
def decoy_games(battles: list[dict], team: int) -> list[tuple[object, float, float]]:
    out = []
    for b in sorted(battles, key=lambda b: moment(b["at"])):
        if (side := side_of(b, team)) is None:
            continue
        mine, theirs = ("win", "loss") if side == "a" else ("loss", "win")
        out += [(None, moment(b["at"]), 1.0 if r == mine else 0.0 if r == theirs else 0.5)
                for r in b.get("resultsA") or [] if r in ("win", "loss", "draw")]
    return out


# Elo's expected share for a team rated `rating` against one rated `other` (docs/unsw/elo.md, The formula).
def expected(rating: float, other: float) -> float:
    return 1 / (1 + 10 ** ((other - rating) / 400))


# How far team `team` beat its ratings' own prediction over battle rows `battles`: each battle's share of games won
# (a draw half) minus Elo's expected share from both teams' ratings at the battle's time (`rating_of(team id, time)`,
# None when unknown: then the rating the list shows, today's, stands in), as {n, mean, z, p}, p the one-sided chance
# of a mean this far above 0 by luck (chance.paired_z over the battles; 1.0 with fewer than 2).
def beats_prediction(battles: list[dict], team: int, rating_of: Callable[[int, float], float | None]) -> dict:
    residuals = []
    for b in battles:
        side = side_of(b, team)
        results = [r for r in b.get("resultsA") or [] if r in ("win", "loss", "draw")]
        if side is None or not results:
            continue
        other, at = "b" if side == "a" else "a", moment(b["at"])
        mine = rating_of(team, at) or (b.get(side) or {}).get("elo")
        theirs = rating_of((b.get(other) or {}).get("id"), at) or (b.get(other) or {}).get("elo")
        if not isinstance(mine, (int, float)) or not isinstance(theirs, (int, float)):
            continue
        won = sum(r == ("win" if side == "a" else "loss") for r in results) + 0.5 * results.count("draw")
        residuals.append(won / len(results) - expected(mine, theirs))
    z = chance.paired_z(residuals)
    return {"n": len(residuals), "mean": sum(residuals) / len(residuals) if residuals else 0.0, "z": z,
            "p": chance.low_p(-z) if len(residuals) >= 2 and z else 1.0}


# `rating_of` for beats_prediction from /ratings' payload: a team's rating at a time, None for a team it lacks.
def ratings_at(board: dict) -> Callable[[int, float], float | None]:
    histories = board.get("histories") or {}
    cache: dict[str, list[tuple[float, float]]] = {}

    def rating_of(team: int, at: float) -> float | None:
        key = str(team)
        if key not in histories:
            return None
        if key not in cache:
            cache[key] = points(histories[key])
        return rating_at(cache[key], at)
    return rating_of


def record_line(label: str, rec: dict, since: float, until: float) -> str:
    share = f"{rec['won'] / rec['games']:.0%}" if rec["games"] else "-"
    return (f"- {label} ({local(since)} to {local(until)}): {rec['battles']} ranked battles, games won {rec['won']:g} "
            f"of {rec['games']} ({share}), rating {rec['rating']:+g} over them")


# context.md's block for one team studied because of `why` ("rise", "new rank 1"): its rise and chance, records
# before and after (overall, against its ratings' prediction as beats_prediction `judged` it, with the chance the
# after-share is this far above the before-share by luck, the decoy check over both sides' ranked games, and by
# opponent met on both sides), the replays and the per-game means of its side, a possible decoy's games left out. `known`: the team was studied in an earlier run, whose findings are earlier/<id>.md.
def team_block(row: dict, why: str, before: list[dict], after: list[dict], complete: bool, games: list[dict],
               judged: dict, error: str | None = None, known: bool = False) -> str:
    lines = [f"## {row['name']} (team {row['id']}, rank {row['rank']}, Elo {row['elo']}; studied for its {why})", "",
             f"Largest climb in the window: {row['climb']:+g} (Elo {row['low_elo']:g} at "
             f"{local(row['low_at'])} to {row['peak_elo']:g} at {local(row['peak_at'])}); net {row['net']:+g} over "
             f"{row['battles']} ranked battles; chance of a climb this large with these battles' rating changes and "
             f"random signs p = {row['p']:.4f}. Before and after split at the climb's low (the window's start when "
             "the climb began before it)." + ("" if row["earlier"] else " It played no rated battle in the window "
                                              "before the window: a new team (or one back) finding its level from "
                                              "its rating then, so the before side may be empty."),
             "Rating path: " + ", ".join(f"{local(t)[5:]} {e:g}" for t, e in row["path"])]
    if known:
        lines.append(f"Studied before: that run's findings are earlier/{row['id']}.md. Report only what changed "
                     "since them.")
    if error:
        return "\n".join([*lines, "", f"Its games could not be fetched: {error}", ""]) + "\n"
    old, new = battle_record(before, row["id"]), battle_record(after, row["id"])
    lines += ["", "Records from the site's battle list (ranked only" + ("" if complete else f"; a side's read "
              f"stopped at {MAX_PAGES // 2} pages, so its record covers the battles nearest the low or the peak")
              + "):",
              record_line("before", old, row["low_at"] - WINDOW, row["low_at"]),
              record_line("after", new, row["low_at"], row["peak_at"])]
    for side in ("before", "after"):
        j = judged[side]
        lines.append(f"- {side}, share of games won minus its ratings' prediction: {j['mean']:+.3f} a battle over "
                     f"{j['n']} battles (p {j['p']:.3f} of that much above 0 by luck)")
    if old["games"] and new["games"]:
        p = chance.rates_p(round(new["won"]), new["games"], round(old["won"]), old["games"])
        lines.append(f"- games-won share after above before by luck alone: p = {p:.3f} (one-sided, draws rounded)")
    decoy = decoys.check(decoy_games(before + after, row["id"]))
    decoy_since = decoy["first_at"] if decoy and decoy["flagged"] else None  # its games are no fit (MISSES 103)
    if decoy:
        lines.append("- " + decoys.line(decoy) + (" Its replays below are left out of the per-game means."
                                                  if decoy_since is not None else ""))
    both = sorted(set(old["opponents"]) & set(new["opponents"]))
    if both:
        lines.append("- opponents met on both sides (games won before -> after): " + "; ".join(
            f"{new['opponents'][o][0]} (team {o}) {old['opponents'][o][1]:g}/{old['opponents'][o][2]} -> "
            f"{new['opponents'][o][1]:g}/{new['opponents'][o][2]}" for o in both))
    lines += ["", "Replays (file, their side, period, opponent, map, their result):"]
    for g in games:
        lines.append(f"- {g['file']}  side {g['side']}  {g['period']}  vs {g['opponent']}  {g['map']}  "
                     f"{'won' if g['won'] else 'lost' if g['won'] is False else 'drew'}")
    if not games:
        lines.append("- none downloaded")
    bceval_read, caveat = online.bceval_module(), True
    for period in ("before", "after"):
        facts = [g["facts"] for g in games if g["period"] == period
                 and (decoy_since is None or moment(g["at"]) < decoy_since)]
        if facts:
            lines += ["", f"Per-game means of their side, {period} ({len(facts)} games; tools/replays.py summary):",
                      "```json", json.dumps(maintenance.mean_facts(facts), indent=1, ensure_ascii=False), "```"]
        # bceval's turning points from their side (bceval_read.brief), with its caveat the first time.
        if turning := bceval_read.brief([g.get("bceval") for g in games if g["period"] == period
                                         and g["won"] is not True], f"losses and draws {period}"):
            lines += ["", turning + (f" {bceval_read.CAVEAT}" if caveat else "")]
            caveat = False
    return "\n".join(lines) + "\n\n"


# Downloads team `row`'s chosen battles into `replays` (at most their games' worth) and summarises each ranked game of
# theirs there from WINDOW before the low to the peak, whichever team's battles fetched it (two risers may have met):
# [{file, side, period, opponent, map, won, at, facts, summary}], oldest first, each game's period from its own
# request time. A replay whose summary fails or whose checks fail is left out.
def fetch_games(row: dict, before: list[dict], after: list[dict], replays: Path,
                tool: Callable[[list[str]], str] = run_tool) -> list[dict]:
    picked = chosen(before, after)
    if not picked:
        return []
    tool(["fetch", "--battle", *(str(b["replayId"]) for b in picked), "--max-games",
          str(len(picked) * GAMES_PER_BATTLE), "--out", str(replays)])
    index = replays / "index.jsonl"
    games = []
    for entry in maintenance.read_jsonl(index):
        at = entry.get("requestedAt")
        file = replays / f"{entry.get('match')}.replay"
        if (row["id"] not in (entry.get("teamAId"), entry.get("teamBId")) or entry.get("ranked") is not True
                or not at or not row["low_at"] - WINDOW <= moment(at) <= row["peak_at"] or not file.is_file()):
            continue
        try:
            summary = json.loads(tool(["summary", str(file)]))
        except (RuntimeError, ValueError, subprocess.SubprocessError):
            continue
        if not summary.get("checks", {}).get("ok"):
            continue
        side = "A" if entry.get("teamAId") == row["id"] else "B"
        other = "B" if side == "A" else "A"
        won = summary["teams"][side].get("won")
        games.append({"file": str(file), "side": side, "period": "before" if moment(at) < row["low_at"] else "after",
                      "opponent": f"{summary['teams'][other].get('name')} (team {summary['teams'][other].get('id')})",
                      "map": summary["map"]["name"], "won": won, "at": at,
                      "facts": summary["teams"][side], "summary": summary,
                      # team_block's turning points in its losses and draws (never raises; None while bceval is off)
                      "bceval": None if won is True else online.bceval_module().game(file, side)})
    return sorted(games, key=lambda g: (moment(g["at"]), g["file"]))


def champion_file(name: str) -> str:
    done = subprocess.run(["git", "show", f"loop/champion:{name}"], cwd=REPO, capture_output=True, text=True)
    return done.stdout if done.returncode == 0 else f"(could not read {name} from loop/champion)\n"


def copy_text(path: Path, missing: str) -> str:
    return path.read_text() if path.exists() else missing


# The run folders prune bounds, each with the runs it keeps: replays (the trusted side's), the worker's folders and
# their prompts and output.
def run_folders(state: Path) -> tuple[tuple[Path, int], ...]:
    return ((state / "riser-replays", KEEP_RUNS), (state / "risers", KEEP_FOLDERS),
            (state / "worker-io/risers", KEEP_FOLDERS))


# Keeps each of run_folders to its newest runs (names are time_ns, so they sort by age); a worker's own flags on its
# folder are undone first (meta.unlock).
def prune(state: Path) -> None:
    for folder, keep in run_folders(state):
        runs = sorted((p for p in folder.glob("*") if p.is_dir() and not p.is_symlink()), key=lambda p: p.name)
        for old in runs[:-keep]:
            try:
                meta.unlock(old)
            except OSError:
                pass
            shutil.rmtree(old, ignore_errors=True)


# The top team in /ratings' ladder other than dev teams and us: the rank 1 whose change starts a study.
def leader(board: dict) -> int | None:
    ladder = [t for t in board.get("ladder") or [] if isinstance(t, dict) and t.get("rank") and not t.get("dev")
              and str(t.get("id")) != OUR_TEAM]
    return min(ladder, key=lambda t: t["rank"])["id"] if ladder else None


# The teams a check looks at, as (row, why): first the new rank 1 when it changed hands since the last check
# (`record["leader"]`) and wasn't studied in the last EVERY, then each passing rise neither studied nor judged K alone
# in the last EVERY, smallest p first, at most MAX_CHECKED. A new rank 1 whose rise also passed is studied as the
# new rank 1, so the wins test never skips it.
def candidates(record: dict, rows: list[dict], top: int | None, now: float) -> list[tuple[dict, str]]:
    new = top is not None and record.get("leader") not in (None, top) and fresh(record, top, now)
    first = [(r, "new rank 1") for r in rows if new and r["id"] == top]
    rises = [(r, "rise") for r in rows if r["picked"] and not (first and r["id"] == top)
             and fresh(record, r["id"], now) and fresh(record, r["id"], now, "k_only")]
    return first + rises[:MAX_CHECKED]


# Opens a run when a check is due: reads /ratings, picks the new rank 1 and the rises (candidates) and, for each rise
# that beat its ratings' prediction after its low (beats_prediction; a new rank 1 either way), fetches and summarises
# its games and writes the worker's folder, at most MAX_TEAMS; it reads no more battles once MAX_TEAMS are studied.
# Only a studied team counts as `seen`; a rise on K alone goes in `k_only`; a team whose battles couldn't be read, or
# over the cap, is looked at again at the next check, and a new rank 1 not studied keeps its trigger (the old leader
# stays recorded). Returns the folder, or None (not due, nobody to study, a failed read, or `stopped()` mid-fetch:
# that run's folders are deleted and the check is retried after RETRY_S). Writes its own alerts: "RISERS none" when a
# candidate rose on K alone, or once a day when there was nobody. Runs in start_fetch's background process.
def prepare(state: Path, now: float, stopped: Callable[[], bool] = lambda: False,
            board: Callable[[], dict] = ratings, tool: Callable[[list[str]], str] = run_tool) -> Path | None:
    with meta.locked(state / "risers"):
        record = read(state)
        if not due(record, now):
            return None
        window = window_of(record)
        save(state, {**record, "attempt_at": now})
        try:
            payload = board()
            rows, end, alpha = pick(payload, window)
            top = leader(payload)
        except (Exception, SystemExit) as error:  # SystemExit: ladder.api_key without a key
            return failed_read(state, now, f"{type(error).__name__}: {error}")
        looked = candidates(record, rows, top, now)
        confirm = chance.TRIGGER_P / max(1, sum(why == "rise" for _, why in looked))
        seen, k_only = ({k: t for k, t in (record.get(key) or {}).items() if now - t < EVERY}
                        for key in ("seen", "k_only"))
        summary = {"window_h": window / 3600, "end": end, "alpha": alpha, "looks": len(rows), "leader": top,
                   "passed": [{k: r[k] for k in ("id", "name", "climb", "p")} for r in rows if r["picked"]]}
        base = {**record, "attempt_at": now, "checked_at": now, "leader": top, "error": None, "failing_since": None,
                "last_pick": summary}
        name = str(time.time_ns())
        work, replays = state / "risers" / name, state / "riser-replays" / name
        studied, skipped, blocks = [], [], []
        rating_of = ratings_at(payload)
        earlier = {k: v for k, v in (record.get("studied_in") or {}).items() if Path(str(v)).is_dir()}  # pruned
        for row, why in looked:
            if len(studied) >= MAX_TEAMS:
                skipped.append(f"{row['name']} ({row['id']}): over MAX_TEAMS this run, looked at again next check")
                continue
            if stopped():
                shutil.rmtree(work, ignore_errors=True)
                shutil.rmtree(replays, ignore_errors=True)
                return None
            try:
                before, after, complete = team_battles(row, tool)
            except Exception as error:
                skipped.append(f"{row['name']} ({row['id']}): its battles could not be read "
                               f"({type(error).__name__}: {str(error)[-200:]})")
                continue
            judged = {"before": beats_prediction(before, row["id"], rating_of),
                      "after": beats_prediction(after, row["id"], rating_of)}
            if why == "rise" and judged["after"]["p"] > confirm:
                k_only[str(row["id"])] = now
                skipped.append(f"{row['name']} ({row['id']}): rose {row['climb']:+g} but its share of games won beat "
                               f"its ratings' prediction by {judged['after']['mean']:+.2f} over {judged['after']['n']} "
                               f"ranked battles after its low (p {judged['after']['p']:.3f} > {confirm:.4f}): a new "
                               "bot's K more than its play")
                continue
            if not studied:
                (work / "teams").mkdir(parents=True)
                (work / "earlier").mkdir()
                replays.mkdir(parents=True)
                prune(state)
            last = Path(str(earlier.get(str(row["id"]), ""))) / "findings.md"
            known = False
            if earlier.get(str(row["id"])) and last.is_file():  # a copy: the worker reads no other run's folder
                try:
                    (work / "earlier" / f"{row['id']}.md").write_text(meta.worker_file(last))
                    known = True
                except OSError:
                    pass
            try:
                games = fetch_games(row, before, after, replays, tool)
                blocks.append(team_block(row, why, before, after, complete, games, judged, known=known))
                (work / "teams" / f"{row['id']}.json").write_text(json.dumps(
                    [{k: v for k, v in g.items() if k != "facts"} for g in games], ensure_ascii=False, indent=1))
            except Exception as error:
                blocks.append(team_block(row, why, before, after, complete, [], judged,
                                         f"{type(error).__name__}: {str(error)[-300:]}", known=known))
            seen[str(row["id"])] = now
            studied.append(row)
        base.update(seen=seen, k_only=k_only)
        if any(why == "new rank 1" for _, why in looked) and top not in {r["id"] for r in studied}:
            base["leader"] = record.get("leader")  # its trigger holds until it is studied
        if not studied:
            if skipped or now - record.get("reported_at", 0) >= EVERY:
                meta.alert(state, f"RISERS none: nobody to study in the past {window / 3600:g} h ({len(rows)} teams "
                                  f"looked at, {len(summary['passed'])} rises past p <= {alpha:.4f}"
                                  + (f"; {'; '.join(skipped)}" if skipped else "") + ")")
                base["reported_at"] = now
            save(state, base)
            return None
        (meta.protected_io(state, "risers", work) / "PROMPT.md").write_text(meta.with_idea_rule(PROMPT.read_text()))
        (work / "context.md").write_text(context(state, rows, end, window, alpha, blocks, skipped))
        (work / "backlog.md").write_text(meta.waiting_ideas(state))
        (work / "champion-params.hpp").write_text(champion_file("bot/params.hpp"))
        (work / "bot-rules.md").write_text(champion_file("bot/CLAUDE.md"))
        for copy, missing in (("study.md", "(no study yet)\n"), ("playbook.md", "(no playbook yet)\n"),
                              ("briefing.md", "(no briefing yet)\n")):
            (work / copy).write_text(copy_text(state / copy, missing))
        base["studied_in"] = {**earlier, **{str(r["id"]): str(work) for r in studied}}
        save(state, {**base, "pending": str(work), "replays": str(replays), "prepared_at": time.time(),
                     "studying": [{"id": r["id"], "name": r["name"]} for r in studied]})
        return work


def failed_read(state: Path, now: float, reason: str) -> None:
    record = read(state)
    first = not record.get("failing_since")
    save(state, {**record, "failed_at": now, "retry_at": None, "error": reason[:500],
                 "failing_since": record.get("failing_since") or now})
    if first:  # later failures in the streak wait for health.riser_study
        meta.alert(state, f"RISERS failed: the rating read failed, retried after an hour: {reason[:300]}")
    return None


def context(state: Path, rows: list[dict], end: float, window: float, alpha: float, blocks: list[str],
            skipped: list[str]) -> str:
    python = REPO / ".venv/bin/python"
    lines = [f"# Riser study: the past {window / 3600:g} h to {local(end)} (the rating read, this machine's time)", "",
             f"Watch tools: {python} {REPO / 'tools/watch.py'} ...   and   {python} {REPO / 'tools/replays.py'} "
             f"summary|deaths|lengths ...   and   {python} {REPO / 'tools/blunders.py'} ...",
             f"Champion code: {state / 'champion/bot'}; its params and editing rules: champion-params.hpp, "
             f"bot-rules.md here. Game rules: {REPO / 'docs/unsw/README.md'}.", "",
             f"The pick: the top {len(rows)} ranked teams by rating (dev teams and us left out), each team's largest "
             "climb in the window against its own battles' rating changes with random signs; passing at p <= "
             f"{alpha:.4f} (chance.TRIGGER_P over {len(rows)} teams), and studied only when the ranked battles after "
             "its low also won more games than both teams' ratings predicted (a new bot's K of 96 turns a small "
             "overperformance into a big rise); a new rank 1 is studied either way. Before and after split at each "
             "climb's low, by time: the site names no submissions since ~19:30 AEST 2026-09-28, so a team may have "
             "changed bots more than once on either side. Ranked games only: unranked ones may be decoys.", "",
             "| team | rank | Elo | climb | net | battles | p | passed |", "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['name']} ({r['id']}) | {r['rank']} | {r['elo']} | {r['climb']:+g} | {r['net']:+g} | "
                     f"{r['battles']} | {r['p']:.4f} | {'yes' if r['picked'] else ''} |")
    if skipped:
        lines += ["", "Looked at but not studied:", *(f"- {line}" for line in skipped)]
    return "\n".join(lines) + "\n\n" + "".join(blocks)


# Validates the run's ideas.jsonl, queues them, and closes the run.
def apply(state: Path, work: Path) -> str:
    with meta.locked(state / "risers"):
        record = read(state)
        if Path(record.get("pending", "")).resolve() != work.resolve():
            raise ValueError("not the pending riser study")
        if not (work / "findings.md").exists():
            return f"RISERS incomplete: {work / 'findings.md'} is missing"
        meta.worker_file(work / "findings.md")  # a link or special file is no report
        ideas = work / "ideas.jsonl"
        refused: list[tuple[str, str]] = []
        proposals = meta.valid_proposals(meta.worker_file(ideas).splitlines() if ideas.exists() else [], MAX_IDEAS,
                                         refused=refused, ideas=meta.live_ideas(state))
        meta.report_refused(state, meta.protected_io(state, "risers", work), f"riser study {work.name}", refused)
        added = meta.append_ideas(state, proposals, f"risers:{work.name}") if proposals else []
        keep = {k: record[k] for k in (*CLOCK, "abandoned") if k in record}
        save(state, {**keep, "finished_at": time.time(), "reported_at": time.time(), "last": str(work), "added": added})
        hours = (record.get("last_pick") or {}).get("window_h") or 0
        return (f"RISERS applied: {len(added)} ideas {added} from the riser study of the past {hours:g} h ({work}); "
                f"decide each finding in {work / 'findings.md'} and each idea's applied/ entry (loop/decisions.py)")


alive = meta.alive


# The upkeep tick's step (maintenance.tick), for a Claude loop only (`models`): applies a finished worker, relaunches
# a failed one after RETRY_S (or a quota limit's reset, meta.when_due), launches a prepared run, or starts a due
# check in the background (start_fetch). Never waits for the check or the worker. One caller at a time
# (meta.only_one). Returns a line to log, or None.
def tick(state: Path, models: bool = True, now: float | None = None) -> str | None:
    if not models:
        return None
    with meta.only_one(state / "risers") as mine:
        return launch_or_collect(state, time.time() if now is None else now) if mine else None


# Whether process `pid` still runs (meta.alive, which reaps it first when this process started it).
def running(pid: object) -> bool:
    return alive(pid)


def launch_or_collect(state: Path, now: float) -> str | None:
    record = read(state)
    if "first_seen" not in record:  # health.riser_study's first-firing check starts here
        record = {**record, "first_seen": now}
        save(state, record)
    work = Path(record["pending"]) if record.get("pending") else None
    held = quota.held(state, "risers", now)  # the usage level: collect only, start nothing (loop/quota.py)
    if work is None:
        return None if held else start_fetch(state, record, now)
    if running(record.get("pid")):
        return None
    if record.get("pid"):  # the worker has exited
        return collect(state, work)
    if held or now < meta.when_due(record, "failed_at", RETRY_S):
        return None
    try:
        folder = meta.protected_io(state, "risers", work)
        meta.unlock(work)
        settings = meta.worker_settings(state, "risers", work)
        command = meta.in_folder(work, meta.private_tmp(meta.worker_command(work, settings,
                                                                            meta.worker_reads(state, "risers"), usage="risers")))
        meta.brief(state, work, folder)
        prompt, out, err = meta.worker_io(folder)
        try:
            proc = subprocess.Popen(command, cwd=folder, env=meta.worker_env(), stdin=prompt, stdout=out, stderr=err,
                                    start_new_session=True)
        finally:
            for fd in (prompt, out, err):
                os.close(fd)
    except Exception as error:
        return failed(state, work, f"could not launch: {type(error).__name__}: {error}")
    save(state, {**read(state), "pid": proc.pid, "launched_at": time.time()})
    return f"riser study launched in the background: {work}"


# Starts a due check (`risers.py prepare`, the trusted side with the site key, never a worker) in its own process,
# its output in risers-fetch.log, unless one started in the last RETRY_S: a check takes minutes, and one that died
# before recording its own start would otherwise restart every minute (a later one still running waits on prepare's
# lock, then finds itself not due). The next tick after it prepares a run launches that run's worker.
def start_fetch(state: Path, record: dict, now: float) -> str | None:
    if not due(record, now) or now - record.get("fetch_at", 0) < RETRY_S:
        return None
    with (state / "risers-fetch.log").open("w") as log:
        proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "prepare", str(state)], cwd=REPO,
                                stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    save(state, {**read(state), "fetch_pid": proc.pid, "fetch_at": now})
    return f"riser study check started in the background (pid {proc.pid}, {state / 'risers-fetch.log'})"


# Applies the pending run `work` whose worker has exited, in a fresh process of main's code (meta.fresh_line), or
# records the failed launch. Writes the outcome to host-alerts.log and returns it.
def collect(state: Path, work: Path) -> str:
    try:
        meta.unlock(work)
        message = meta.fresh_line("risers.py", ["apply", str(state), str(work)],
                                  ("RISERS applied", "RISERS incomplete"), lambda: applied_line(state, work))
    except Exception as error:
        message = f"RISERS incomplete: {type(error).__name__}: {error}"
    if message.startswith("RISERS incomplete"):
        try:
            why = meta.session_error(meta.protected_io(state, "risers", work))
        except OSError as error:
            why = f"its session output couldn't be read ({type(error).__name__})"
        return failed(state, work, f"the worker failed: {f'{why}; ' if why else ''}{message}")
    meta.alert(state, message)
    return message


# Records a failed launch of the pending run `work`: retried after RETRY_S, abandoned at meta.MAX_LAUNCHES counted
# failures (a provider's quota error is retried once the limit resets but not counted, meta.after_failure,
# meta.when_due). Alerts and returns the line.
def failed(state: Path, work: Path, reason: str) -> str:
    with meta.locked(state / "risers"):
        record = read(state)
        if Path(record.get("pending", "")).resolve() != work.resolve():
            return f"riser study {work} is no longer pending; nothing recorded ({reason})"
        record, abandoned = meta.after_failure(record, work, reason, (*CLOCK, "last", "finished_at", "added"))
        save(state, record if abandoned else {**record, "failed_at": time.time()})
    if abandoned:
        message = f"RISERS abandoned after {meta.MAX_LAUNCHES} failed launches ({work}): {reason}"
    elif meta.provider_limited(reason):
        message = (f"RISERS waiting out a provider quota limit, retried {meta.retry_when(record)} (not counted) "
                   f"({work}): {reason}")
    else:
        message = f"RISERS failed: the worker failed; retrying after an hour ({work}): {reason}"
    meta.alert(state, message)
    return message


def applied_line(state: Path, work: Path) -> str:
    try:
        return apply(state, work)
    except Exception as error:
        return f"RISERS incomplete: {type(error).__name__}: {error}"


# `risers.py pick [--hours H]`: the read-only dry run, one GET /ratings, printing what a run would pick.
def dry_run(hours: float) -> None:
    rows, end, alpha = pick(ratings(), hours * 3600)
    print(f"past {hours:g} h to {local(end)}; a pick needs p <= {alpha:.4f} ({len(rows)} teams)")
    for r in rows:
        print(f"{'PICK' if r['picked'] else '    '} p {r['p']:.4f}  #{r['rank']:<3} {r['name']} ({r['id']}) Elo "
              f"{r['elo']}: climb {r['climb']:+g} ({local(r['low_at'])} -> {local(r['peak_at'])}), net "
              f"{r['net']:+g} over {r['battles']} battles")


# `risers.py --studied TEAM...`: records teams studied by hand now (`seen`), so no check studies them for EVERY.
def mark_studied(state: Path, teams: list[str], now: float) -> str:
    with meta.locked(state / "risers"):
        record = read(state)
        save(state, {**record, "seen": {**(record.get("seen") or {}), **{str(int(t)): now for t in teams}}})
    return f"riser study: teams {', '.join(teams)} recorded as studied, skipped until {local(now + EVERY)}"


if __name__ == "__main__":
    if sys.argv[1:2] == ["apply"] and len(sys.argv) == 4:
        print(applied_line(Path(sys.argv[2]), Path(sys.argv[3])))
    elif sys.argv[1:2] == ["prepare"] and len(sys.argv) == 3:
        import publishing
        home = Path(sys.argv[2])
        print(prepare(home, time.time(), lambda: publishing.stopped(home)) or "riser study: no run prepared")
    elif sys.argv[1:2] == ["pick"]:
        dry_run(float(sys.argv[3]) if sys.argv[2:3] == ["--hours"] else FIRST_WINDOW / 3600)
    elif sys.argv[1:2] == ["--studied"] and sys.argv[2:]:
        from home import loop_home
        print(mark_studied(loop_home(), sys.argv[2:], time.time()))
    else:
        sys.exit("usage: risers.py pick [--hours H] | risers.py --studied TEAM_ID... | risers.py prepare <loop home> "
                 "| risers.py apply <loop home> <run folder>")
