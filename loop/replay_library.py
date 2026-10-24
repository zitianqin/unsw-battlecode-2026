"""The replay library: one index, online-replays/library.json, of the saved online games a rate or read may count, for
coders to read in place (sig, 2026-09-29: "Fitting should run on more"; a clone's .online-sample/ and .leaders/ hold a
sample). Coders' sandboxes read online-replays/ and study-replays/ and nothing else under the loop's state
(driver.READABLE_STATE); tools/replays.py's library_games and `replays.py library` read this index.

What counts, each rule the rest of the loop's (MISSES 55: a rate or fit counts games chosen blind to their result):
- ours ("online"): every saved game of every version whose file is in the result-blind sample (not
  online.file_selected: no loss kept because we lost, no dev file left beside pruned wins), not against a dev team
  (online.against_dev), with its replay on disk, whatever the opponent: a rate over them is our rate over result-blind
  non-dev games;
- leaders' ("leaders"): the study's saved games (study-replays/index.jsonl and each valuenet/*/index.jsonl) of a team
  the study has listed as a leader (any study-history.jsonl row; a valuenet folder's team, the one in all its rows),
  one row per leader side, with its replay on disk.
Decoys (sig, 2026-09-29: teams sometimes swap their good bot for a bad one in unranked games; CLAUDE.md, Cutlery's
unranked are a fake bot): a bot's game is ranked (ranked requests and autoscrims), or unranked with a submission its
team also played a ranked game with in the games we hold, or else possibly a decoy. The site stopped naming
submissions on 2026-09-28 (online.API_GONE), so every unranked game since is possibly one. A decoy fakes its own
team's play, not ours: our games against one stay in "games", marked "decoy_risk": true (the opponent may have been
sandbagging, so leave them out of a read of how opponents play); a leader's possibly-decoy games go under the leaders'
"decoy_risk", out of its "games".

Size: one compact row (about 350 bytes) per saved replay, rewritten whole, so a thousandth of the replays' own size
(prune bounds those). refresh rebuilds it only when a source index is newer than it (upkeep's "replay library" step,
each tick), else leaves it as it is; health.replay_library alerts when it falls behind.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import maintenance
import online

LIBRARY = Path("online-replays/library.json")


def _result(winner, side: str) -> str:
    winner = str(winner or "").upper()
    return "draw" if not winner else "won" if winner == side else "lost"


# Each of our versions' index (online-replays/<submission>/index.json).
def version_indexes(state: Path) -> list[Path]:
    return sorted(p for p in (state / "online-replays").glob("*/index.json") if p.parent.name.isdigit())


# The index files the library is built from, for refresh's staleness test.
def sources(state: Path) -> list[Path]:
    study = state / "study-replays"
    return [*version_indexes(state), study / "index.jsonl", *sorted(study.glob("valuenet/*/index.jsonl")),
            state / "study-history.jsonl"]


# The leader study's index rows, each with where it came from and the team it was fetched for (None: the main study
# index, whose leaders come from study-history.jsonl), each match once.
def study_rows(state: Path) -> list[tuple[dict, str, int | None]]:
    folder = state / "study-replays"
    found, seen = [], set()
    for index in [folder / "index.jsonl", *sorted(folder.glob("valuenet/*/index.jsonl"))]:
        rows = [r for r in maintenance.read_jsonl(index) if isinstance(r, dict) and r.get("match")]
        common = set.intersection(*({r.get("teamAId"), r.get("teamBId")} for r in rows)) if rows else set()
        team = next(iter(common)) if index.parent != folder and len(common) == 1 else None
        source = str(index.parent.relative_to(folder)) if index.parent != folder else "study"
        for row in rows:
            if row["match"] not in seen and (index.parent / f"{row['match']}.replay").is_file():
                seen.add(row["match"])
                found.append(({**row, "replay": str(index.parent / f"{row['match']}.replay")}, source, team))
    return found


def build(state: Path) -> dict:
    """The library's contents (without its build time) from the state's indexes as they are now."""
    try:
        dev = online.dev_ids(json.loads((state / "online.json").read_text()))
    except (OSError, ValueError, AttributeError, TypeError):
        dev = set()
    indexes = []
    for path in version_indexes(state):
        try:
            indexes.append(json.loads(path.read_text()))
        except (OSError, ValueError):  # a version's index being rewritten: the next refresh reads it
            continue
    studied = study_rows(state)
    # Every (team, submission) a ranked game we hold shows: our games' opponents and both sides of the study's.
    ranked = {(g.get("opponent"), g["opponent_submission_id"]) for index in indexes for g in online.all_games(index)
              if g.get("ranked") is True and g.get("opponent_submission_id") is not None}
    ranked |= {(row.get(f"team{s}Id"), row[f"submission{s}Id"]) for row, _, _ in studied for s in "AB"
               if row.get("ranked") is True and row.get(f"submission{s}Id") is not None}
    counts = lambda ranked_game, team, submission: ranked_game is True or (team, submission) in ranked

    ours = {"games": [], "left_out": {"file_selected": 0, "dev": 0, "no_file": 0, "no_summary": 0}}
    for index in indexes:
        for g in index.get("games", []):
            if not isinstance(g, dict):
                continue
            why = "file_selected" if online.file_selected(g) else "dev" if online.against_dev(g, dev) else \
                "no_file" if not Path(g.get("replay") or "").is_file() else "no_summary" if "summary" not in g else None
            if why:
                ours["left_out"][why] += 1
                continue
            row = {"replay": g["replay"], "id": g["id"], "submission_id": g.get("submission_id"), "side": g["side"],
                   "opponent": g.get("opponent"), "opponent_submission_id": g.get("opponent_submission_id"),
                   "map_id": g.get("map_id"), "map": (g["summary"].get("map") or {}).get("name"),
                   "ranked": g.get("ranked"), "requested_at": g.get("requested_at"),
                   "result": _result(g["summary"]["result"]["winner"], g["side"]),
                   "decoy_risk": not counts(g.get("ranked"), g.get("opponent"), g.get("opponent_submission_id"))}
            ours["games"].append(row)

    leader_ids = {int(t["id"]) for r in maintenance.read_jsonl(state / "study-history.jsonl")
                  for t in r.get("leaders") or [] if isinstance(t, dict) and t.get("id") is not None}
    leaders = {"games": [], "decoy_risk": [], "left_out": {"no_leader": 0}}
    for row, source, team in studied:
        sides = [s for s in "AB" if row.get(f"team{s}Id") in ({team} if team is not None else leader_ids)]
        if not sides:
            leaders["left_out"]["no_leader"] += 1
        for side in sides:
            other = "B" if side == "A" else "A"
            game = {"replay": row["replay"], "id": row["match"], "source": source, "team_id": row.get(f"team{side}Id"),
                    "team": row.get(f"team{side}Name"), "submission_id": row.get(f"submission{side}Id"), "side": side,
                    "opponent": row.get(f"team{other}Id"), "opponent_name": row.get(f"team{other}Name"),
                    "map": row.get("map"), "ranked": row.get("ranked"), "autoscrim": row.get("autoscrim"),
                    "requested_at": row.get("requestedAt"), "result": _result(row.get("winner"), side)}
            ok = counts(row.get("ranked"), game["team_id"], game["submission_id"])
            leaders["games" if ok else "decoy_risk"].append(game if ok else {**game, "decoy_risk": True})
    return {"online": ours, "leaders": leaders}


def refresh(state: Path) -> str:
    """Rebuilds the library when a source index is newer than it; returns a line to log when it wrote one. Rewritten
    only when its contents changed. Either way its time is set to the newest source's as read before the build, so a
    source changed while it builds is newer than it and read at the next refresh."""
    path = state / LIBRARY
    newest = max((p.stat().st_mtime for p in sources(state) if p.exists()), default=0)
    if path.exists() and path.stat().st_mtime >= newest:
        return ""
    library = build(state)
    try:
        old = json.loads(path.read_text())
        old.pop("built_at", None)
    except (OSError, ValueError, AttributeError):
        old = None
    line = ""
    if old != library:
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(json.dumps({"built_at": time.time(), **library}, separators=(",", ":")) + "\n")
        temporary.replace(path)
        ours, leaders = library["online"], library["leaders"]
        line = (f"replay library: online {len(ours['games'])} games "
                f"({sum(g['decoy_risk'] for g in ours['games'])} against a possible decoy), "
                f"leaders {len(leaders['games'])} games ({len(leaders['decoy_risk'])} possible decoys left out)")
    os.utime(path, (newest, newest))
    return line
