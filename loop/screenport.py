"""Screen ports (method note M24, MISSES 16 class): an opponent-screen entry built on an old champion that still plays
level with today's is a search result. It is missing every promotion since its base, so its param values are probably
worth having: `hunter` (v0018 with wider hunting) scored 0.43-0.61 against every champion from E0089's to v0029 while
the other old-base entries scored 0.07-0.28, and nobody ported its values until a review queued I261 by hand.

For each referee/gauntlet.toml entry (main's; not one whose params.hpp declares no params, a bot that isn't ours) whose
base champion (the newest v* tag its commit contains) is at least MIN_PROMOTIONS promotions older than the current
champion, and whose score against the current champion is at least PORT_AT over MIN_GAMES games or more, it queues ONE
priority-2 idea porting the mechanism behind the entry's param diff (its values that differ from its base champion's
params.hpp) onto the current champion, each part behind its own switch (a flipped switch as it is, a moved number as a
rule of what the bot observes or with a Constants: reason: proposal), with source `screen-port:<entry>:<champion>` and a `screen_port` record (the entry, champion, score,
games and values). A value the variant sweep of this champion played alone (sweep.variants: 0, 4x, a never or always,
a switch flip; a round past the game's end counts as its never: mergeRound 499, or for foldRound and beaconRound their
switch off, NEVER_SWITCH) is left out only when that variant scored at least the entry's score (drop_swept, method note
M37: the sweep queues only at about +40 Elo, so a value it played and didn't queue can still gain less); when nothing
is left, nothing is queued. The port waits whole while another idea in flight or to do at WAIT_PRIORITY or better
sets one of its values (waits_on: I203 set the breeder's mergeRound 499 alone), since the entry's score is of its
values together. It skips an entry while a port of it (that source, any champion) or an idea whose title names it as a
screen entry ("like the hunter screen entry") waits or is in flight, when such a named idea was tested on this
champion, and when either kind tried the same values, unless the entry now scores RISE more than it did then (see
covered).

Code-switch ports (REFLECT 2026-09-28 20:52 F7): an entry that extends its base champion (every base param still in
its params.hpp) and adds switches the base lacks (new_switches: `harvester` is v0053 plus P.harvestDrops and its code)
is built on code, not values, so neither its age nor `port = false` (which bars a params port) applies; a bot that
isn't ours (loneking, the starter, one written from scratch) extends nothing and never takes this route: once it scores above half against the current champion at a
chance sign_p <= chance.TRIGGER_P (it beats the champion), it gets one idea porting its code behind those switches
(code_port), at priority 1 and marked next, unless the champion already has every switch at the entry's value or
covered() finds a port of it; short of that it goes the params route above like any other entry. harvester won 29 of 48 against v0053 and waited for sig to ask (I747).

"Score against the current champion" is the entry's points in the latest screen of the champion itself: the screen of a
result tested against it (its `base` column), else its late screen (rescreen.json), else the screen of the result that
promoted it (its `candidate` column), unless the champion's games against the entry kept on its shared map set
(set_scores: the games those screens' base columns read, and the prewarm's) are at least as many, which they are for
every entry the prewarm plays (all but the rotation's): a two-stage screen row can hold 50 games and a promising
result has no screen (2026-09-27). A rotation entry has 100 in the set only when a screen played its maps itself; one
whose SPRT trials covered all 50 maps keeps its score in the screen row, which a two-stage screen then fills to 50
maps (every reused trial is in its first stage). The champion itself
rather than its candidates, since a candidate is another bot and most are discarded. One source only, not pooled:
results tested against one champion share its maps and reuse its screen games, so pooling them would count the same
games several times.

The upkeep tick (maintenance.tick) runs `tick`, which does the work only when results.jsonl, rescreen.json, the
champion, main's gauntlet.toml or baselines.json (the prewarm's progress) changed since its last run: once per screened
result, promotion or prewarm stage, for both providers, without parsing results.jsonl every minute. A run that
raises, or an idea that fails validation, is recorded in <loop home>/screenport.json, which health.py alerts on, as it
does when the step stops running. stats.md shows every entry's latest score against the current champion
(`entry_scores`).

    .venv/bin/python loop/screenport.py check [--state S]   # what it decides today, queueing and writing nothing
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import chance
import meta

REPO = Path(__file__).resolve().parent.parent
MIN_PROMOTIONS = 2
PORT_AT = 0.45  # the entry's points a game against the current champion
MIN_GAMES = 100
PRIORITY = 2
HISTORY = 10  # earlier champions' screens quoted in the idea
RISE = 0.05  # a tried port is queued again only when the entry scores this much more (100 games: noise about +-0.05)
NEVER_ROUND = 499  # a round param at this or later acts at most in the last round: the sweep's never (sweep.NOT_4X)
RECORD = "screenport.json"


# ------------------------------------------------------------------------------------------------- scores

# The champion a result was tested against (rescreen.previous: a rebased candidate was tested against the newer one).
def previous(row: dict) -> str:
    return (row.get("evaluation") or {}).get("base") or row.get("base") or ""


def screen(row: dict) -> list[dict]:
    return [o for o in (row.get("pool") or {}).get("opponents", []) if isinstance(o, dict) and "name" in o]


# The games a side played against screen entry `o` in result `row`'s screen: both sides of each of its maps, fewer when
# a two-stage screen stopped at its first stage (driver.check_pool's `stopped_at`).
def screen_games(row: dict, o: dict) -> int:
    return 2 * int(o.get("stopped_at") or (row.get("pool") or {}).get("pairs_per_opponent") or 0)


def lines(path: Path) -> int:
    try:
        return sum(1 for line in path.read_text().splitlines() if line.strip())
    except OSError:
        return 0


# Each screen entry's latest score against `champion` itself: {name: {"score": the entry's points a game, "games",
# "source"}} (see the module docstring for which screen counts). `rows` are results.jsonl's rows in order, `rescreens`
# rescreen.json, `state` the loop home whose rescreens/ folder holds the late screen's games. `keys`: each entry's spec
# key in main's gauntlet.toml (driver.spec_key), so the shared set's games of another build of an entry aren't read.
def entry_scores(rows: list[dict], rescreens: dict, champion: str, state: Path,
                 keys: dict[str, str] | None = None) -> dict[str, dict]:
    found: dict[str, dict] = {}
    promoting = next((r for r in reversed(rows) if r.get("verdict") == "kept" and r.get("candidate") == champion), None)
    if promoting:
        for o in screen(promoting):
            if isinstance(o.get("candidate"), (int, float)):
                found[o["name"]] = {"score": round(1 - o["candidate"], 4), "games": screen_games(promoting, o),
                                    "source": f"{promoting['id']}'s screen, the champion as its candidate"}
    late = rescreens.get(champion, {}) if isinstance(rescreens, dict) else {}
    for name, o in (late.get("done") or {}).items():
        if isinstance(o, dict) and isinstance(o.get("candidate"), (int, float)):
            found[name] = {"score": round(1 - o["candidate"], 4),
                           "games": lines(state / "rescreens" / champion / f"{name}-candidate.jsonl"),
                           "source": "its late screen (rescreen.json)"}
    for row in rows:
        if row is promoting or previous(row) != champion:
            continue
        for o in screen(row):
            if isinstance(o.get("base"), (int, float)):
                found[o["name"]] = {"score": round(1 - o["base"], 4), "games": screen_games(row, o),
                                    "source": f"{row['id']}'s screen, the champion as its base"}
    for name, score in set_scores(state, champion, keys).items():  # at least as many games: the fullest source
        if score["games"] >= (found.get(name) or {}).get("games", 0):
            found[name] = score
    return found


# A shared set's file of the champion's games against one screen entry (driver.screen_baselines): the entry's name,
# the spec key its bot folder carries (driver.spec_key), the entry's and the champion's fixed-key test copies
# (driver.opponent_bot, driver.base_build) if they played them, and "-50" in sets from before 2026-09-26.
SET_FILE = re.compile(r"pool-(?P<name>.+)-(?P<key>[0-9a-f]{12})(?P<own>-ownkey-[0-9a-f]{16})?"
                      r"(?P<build>-fixedkey-[0-9a-f]{16})?-base(-\d+)?\.jsonl")


# Each entry's points a game in the champion's games against it on its shared map set (<state>/shared-maps/
# <champion[:12]>-<epoch>[-<mix>]/, meta.shared_set_name: the current toolkit epoch's set of today's shared mix, the
# prewarm's, when it has one, else the set with the most games), each map and side once and each build of an entry
# (its spec key, `keys` naming main's; its test copy) and of the champion apart. Screens fill it, and the idle prewarm
# plays 50 maps both sides there for every entry outside the rotation; a rotation entry's screen games there are only
# those a screen played itself (one the SPRT's trials fully covered wrote none). Since two-stage screens (2026-09-27)
# a screen row may hold 50 games and a promising result none, so this is the source that usually reaches MIN_GAMES.
def set_scores(state: Path, champion: str, keys: dict[str, str] | None = None) -> dict[str, dict]:
    import maintenance
    folders = sorted(state.glob(f"shared-maps/{champion[:12]}-*"))
    current = state / "shared-maps" / meta.shared_set_name(champion, maintenance.toolchain(state).get("epoch"),
                                                           meta.claim_mix(None))
    found: dict[str, dict] = {}
    for folder in [current] if current in folders else folders:
        seats: dict[tuple[str, str, str | None, str | None], dict[tuple[str, str], str]] = {}
        for path in sorted(folder.glob("pool-*-base*.jsonl")):
            if not (match := SET_FILE.fullmatch(path.name)):
                continue
            if keys and match["name"] in keys and keys[match["name"]] != match["key"]:
                continue  # another build of the entry than main's
            for line in path.read_text().splitlines():
                try:
                    row = json.loads(line) if line.strip() else {}
                except ValueError:  # a row torn by a writer killed mid-line: that game is simply not counted
                    continue
                if row.get("winner") in ("a", "b", "draw") and not row.get("error"):
                    seats.setdefault((match["name"], match["key"], match["own"], match["build"]), {})[
                        (row["map"], row["a_side"])] = row["winner"]
        for (name, *_), winners in seats.items():
            if len(winners) > (found.get(name) or {}).get("games", 0):
                points = sum({"a": 0.0, "b": 1.0, "draw": 0.5}[w] for w in winners.values()) / len(winners)
                found[name] = {"score": round(points, 4), "games": len(winners),
                               "source": f"the champion's games on its shared map set {folder.name}"}
    return found


# The entry's points against each champion it was screened with, in the order the champions were first screened:
# [(champion, result id, points, games)], each champion's latest screen only (its `base` column), since the results
# tested against one champion reuse its screen games.
def history(rows: list[dict], name: str) -> list[tuple[str, str, float, int]]:
    latest: dict[str, tuple[str, str, float, int]] = {}
    for row in rows:
        for o in screen(row):
            if o["name"] == name and isinstance(o.get("base"), (int, float)):
                latest[previous(row)] = (previous(row), row["id"], round(1 - o["base"], 4), screen_games(row, o))
    return list(latest.values())


# ------------------------------------------------------------------------------------------------- the port

def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, text=True, capture_output=True).stdout


# The newest champion version (v0018 is 18) that `commit` contains, or None: an entry's base champion, or the
# champion's own number (driver.promote tags each champion once, in promotion order).
def version(repo: Path, commit: str) -> int | None:
    tags = [int(t[1:]) for t in git(repo, "tag", "--merged", commit, "--list", "v*").split() if t[1:].isdigit()]
    return max(tags, default=None)


def tag(number: int) -> str:
    return f"v{number:04d}"


# An entry's param diff and how it lands on the current champion, from three params.hpp texts: the entry commit's, its
# base champion's and the current champion's, and the entry's `params` overrides. Returns {"port": {name: (current
# value, entry value)} for the params to set, "same": names the champion already has at the entry's value, "missing":
# {name: entry value} for params the champion lacks (the entry's own code)}.
def port(spec_params: dict, entry_text: str, base_text: str, current_text: str) -> dict:
    import sweep
    entry = {p["name"]: p["value"] for p in sweep.read_params(entry_text)} | dict(spec_params)
    base = {p["name"]: p["value"] for p in sweep.read_params(base_text)}
    current = {p["name"]: p["value"] for p in sweep.read_params(current_text)}
    out: dict = {"port": {}, "same": [], "missing": {}}
    for name, value in entry.items():
        if name in base and base[name] == value:
            continue
        if name not in current:
            out["missing"][name] = value
        elif current[name] == value:
            out["same"].append(name)
        else:
            out["port"][name] = (current[name], value)
    return out


# The sweep's finished single variants of `champion` (params.hpp `current_text`): {variant id: (the variant's points a
# game against the champion, games)}, from <state>/sweeps/<champion>/results.jsonl; a variant still to play a pass, or
# one that failed (unscored), is left out. One the sweep decided (its record's "considered") is finished.
def sweep_scores(state: Path, champion: str, current_text: str) -> dict[str, tuple[float, int]]:
    import sweep
    rows = sweep.results(state / "sweeps" / champion)
    listing = [v for v in sweep.variants(current_text)["variants"] if "param" in v]
    record = sweep.read(state)
    finished = set(sweep.done(listing, rows, record.get("considered") if record.get("champion") == champion else None))
    return {v["id"]: (sweep.score(games), len(games)) for v in listing
            if v["id"] in finished and not sweep.failed(rows, v["id"]) and (games := sweep.games(rows, v["id"]))}


# The switch whose flip off is a round's never when the sweep has no round value past the game's end (sweep.NOT_4X:
# 4x foldRound is finalFold = false, 4x beaconRound is kingBeacon = false).
NEVER_SWITCH = {"foldRound": "finalFold", "beaconRound": "kingBeacon"}


# The sweep's variant playing `name` = `new` alone on the champion (params.hpp `current_text`): the one of that value,
# or for a round past the game's end, the round's never (mergeRound 499; for foldRound and beaconRound, their switch
# off, NEVER_SWITCH). None when the sweep has none.
def sweep_variant(name: str, new, current_text: str) -> str | None:
    import sweep
    never = name in sweep.ROUNDS and isinstance(new, (int, float)) and new >= NEVER_ROUND
    for v in (v for v in sweep.variants(current_text)["variants"] if "param" in v):  # not a group's
        if v["param"] == name and (v["value"] == new or (never and v["value"] >= NEVER_ROUND)):
            return v["id"]
        if never and v["param"] == NEVER_SWITCH.get(name) and v["value"] is False:
            return v["id"]
    return None


# Moves `change["port"]` values the sweep of this champion measured alone to change["swept"] {name: why}, when that
# variant scored at least `score`, the entry's (method note M37). The sweep queues a variant only at sweep.QUEUE_AT
# over its 40 self-play games (and level against the seats), about +40 Elo, so a value it plays but doesn't queue may
# still be worth +10: the breeder scored 0.49 against v0034 over 100 games and went unported because the sweep plays
# its mergeRound 499. A value the sweep hasn't finished, or whose variant scored less, stays in the port, with why in
# change["unswept"] {name: why}. `scores` is sweep_scores' (self-play games only) for the champion whose params.hpp is
# `current_text`.
def drop_swept(change: dict, current_text: str, scores: dict[str, tuple[float, int]], score: float) -> dict:
    swept, kept, unswept = {}, {}, {}
    for name, (old, new) in change["port"].items():
        variant = sweep_variant(name, new, current_text)
        if variant and variant in scores and scores[variant][0] >= score - 1e-9:
            points, games = scores[variant]
            swept[name] = (f"the sweep's {variant} scored {points:.2f} over {games} games, at least the entry's "
                           f"{score:.2f}")
            continue
        kept[name] = (old, new)
        if variant and variant in scores:
            unswept[name] = (f"the sweep's {variant} scored {scores[variant][0]:.2f} over {scores[variant][1]} games, "
                             f"under the entry's {score:.2f}")
        elif variant:
            unswept[name] = f"the sweep of this champion hasn't finished {variant} yet"
    return {**change, "port": kept, "swept": swept, "unswept": unswept}


# Whether an idea's title says it sets `name` to `value`: "the breeder's mergeRound 499" (I203), "maxRamSteps 3->6",
# "maxRamSteps=3->6", "mergeRound = 499". Naming the param alone isn't enough: "Tune the merge cascade" or "from
# foldRound, prefer a move with a safe follow-up" (I215) change something else, and the old value of "a->b" isn't set.
def sets_value(idea: dict, name: str, value) -> bool:
    before = r"(?:\s*(?:=|->)\s*|\s+(?:to\s+)?|(?:\s+|\s*=\s*)(?:-?[\d.]+|true|false)\s*->\s*)"
    return bool(re.search(rf"(?<![\w-]){re.escape(name)}{before}{re.escape(literal(value))}(?![\w.])(?!\s*->)",
                          idea.get("title") or ""))


# Ideas a port waits on (waits_on): in flight, or to do at this priority or better. A forgotten priority-4 idea
# naming a value can't hold a port forever.
WAIT_PRIORITY = 2


# Why `entry`'s port (change["port"]) must wait, or None: an idea in flight or to do at WAIT_PRIORITY or better, other
# than a port of it or one titled after it (covered's), sets one of its values (sets_value). The entry's score measured
# its values together, so the port waits whole rather than dropping that value: I203 (the breeder's mergeRound 499
# alone) was being coded as E0135 when M37 asked for the breeder's port. Once that idea is tested the port goes ahead
# with every value.
def waits_on(change: dict, ideas: list[dict], entry: str) -> str | None:
    import sweep
    others = [i for i in ideas if (str(i.get("status", "")).startswith("trying:")
                                   or (i.get("status") == "todo" and (i.get("priority") or 5) <= WAIT_PRIORITY))
              and not str(i.get("source", "")).startswith(f"screen-port:{entry}:") and not names_port(i, entry)]
    for name, (_, new) in change["port"].items():
        # A round past the game's end plays as the sweep's never: mergeRound 100000 is mergeRound 499.
        same = [new] + ([NEVER_ROUND] if name in sweep.ROUNDS and isinstance(new, (int, float))
                        and new > NEVER_ROUND else [])
        for idea in others:
            for value in same:
                if sets_value(idea, name, value):
                    return (f"{idea['id']} ({idea['status']}) sets {name} {literal(value)}, one of its values: the port "
                            "waits for it, since the entry's score is of its values together")
    return None


def values(change: dict) -> dict:
    return {**{name: new for name, (_, new) in change["port"].items()}, **change["missing"]}


# Whether an idea's title names `entry` as a screen entry ("like the hunter screen entry", "Port the `hunter` screen
# entry's values"), not the entry's name anywhere ("Cap the swarm at 40" is no port of the swarm entry).
def names_port(idea: dict, entry: str) -> bool:
    return bool(re.search(rf"(?<![\w-])`?{re.escape(entry)}`? screen (?:entry|opponent)\b", idea.get("title") or "",
                          re.I))


def waiting(idea: dict) -> bool:
    status = str(idea.get("status", ""))
    return status == "todo" or status.startswith("trying:")


# The values a port idea titled like I261 ("hunterMaxLength 5->8, huntMaxDist 6->10 and huntMinEdge 0->-1") set:
# {name: new value}. Read from the title rather than from its result's candidate: a given-up idea has no candidate,
# and a candidate's params can differ from the idea by its coder's own edits or a rebase, while the title states the
# values the idea set out to test.
def title_values(idea: dict) -> dict:
    found = re.findall(r"\b([a-z]\w*) (?:-?[\d.]+|true|false) ?-> ?(-?\d+(?:\.\d+)?|true|false)\b",
                       idea.get("title") or "")
    return {name: new == "true" if new in ("true", "false") else float(new) if "." in new else int(new)
            for name, new in found}


# Why `entry` needs no new port idea of `ported` (values) at `score` on `champion`, or None:
# - a port of it (source screen-port:<entry>:, any champion) or an idea titled after it as a screen entry (names_port)
#   waiting or in flight;
# - such an idea of the same values that isn't waiting (tried, merged or given up), unless the entry now scores at
#   least RISE more than then: a port's `screen_port` score, or for a named idea its score against the champion its
#   latest result was tested on (`score_on(champion)`), else today's;
# - a named idea tested on this champion (with SPRT games), whatever its values.
def covered(ideas: list[dict], rows: list[dict], entry: str, champion: str, ported: dict, score: float,
            score_on) -> str | None:
    for idea in ideas:
        port = str(idea.get("source", "")).startswith(f"screen-port:{entry}:")
        if not (port or names_port(idea, entry)):
            continue
        if waiting(idea):
            return f"{idea['id']} ({idea['status']}) " + ("ports it already" if port else "names it as a screen entry")
        if port:
            record = idea.get("screen_port") or {}
            tested, before, when = record.get("values", ported), record.get("score"), "when it was queued"
        else:
            result = next((row for row in reversed(rows) if (row.get("idea") or {}).get("id") == idea["id"]), None)
            tested, when = title_values(idea), f"against {previous(result)[:7]} ({result['id']})" if result else "now"
            before = score_on(previous(result)) if result else None
        if tested != ported:
            continue
        if not isinstance(before, (int, float)):
            before = score
        if score < before + RISE - 1e-9:
            return (f"{idea['id']} ({idea.get('status')}) tried the same values, the entry scoring {before:.2f} "
                    f"{when}; it scores {score:.2f} now, under {RISE} more")
    for row in rows:
        idea = row.get("idea") or {}
        if not str(idea.get("source", "")).startswith("screen-port:") and previous(row) == champion \
                and names_port(idea, entry) and (row.get("sprt") or {}).get("score") is not None:
            return f"{idea.get('id')} ({row['id']}) was tested on this champion and names it as a screen entry"
    return None


def literal(value) -> str:
    return str(value).lower() if isinstance(value, bool) else str(value)


def category(name: str, current_text: str) -> str:
    import sweep
    section = next((p["section"] for p in sweep.read_params(current_text) if p["name"] == name), "")
    return sweep.category({"section": section})


# The idea porting `entry`'s diff (`change`, drop_swept's output) onto the current champion: its mechanism, each part
# behind its own switch. A switch it flips is a behaviour choice, ported as it is; a number it moves is a hint for the
# behaviour to build as a rule of what the bot observes, the champion's value kept otherwise, and one set as the entry
# has it needs a Constants: reason (sig, 29 Sep: no magic numbers; I968 set feederking's beaconRound 405->350 and
# foldRound 397->350 as numbers). The title and hypothesis name each "a->b" as the entry's move off the champion's
# value, evidence rather than a rule, so meta.bare_thresholds' VALUE_SWAP check passes the idea.
def proposal(spec: dict, change: dict, score: dict, past: list[tuple[str, str, float, int]], base: int, current: int,
             champion: str, current_text: str) -> dict:
    moves = ", ".join(f"{n} {literal(old)}->{literal(new)}" for n, (old, new) in change["port"].items())
    flips = ", ".join(f"{n} {literal(old)}->{literal(new)}" for n, (old, new) in change["port"].items()
                      if isinstance(new, bool))
    hints = ", ".join(f"{n} {literal(old)}->{literal(new)}" for n, (old, new) in change["port"].items()
                      if not isinstance(new, bool))
    code = ", ".join(f"{n} = {literal(v)}" for n, v in change["missing"].items())
    overrides = ", ".join(f"{n} = {literal(v)}" for n, v in (spec.get("params") or {}).items())
    earlier = ", ".join(f"{commit[:7]} {points:.2f} ({eid}" + (f", {games} games)" if games != MIN_GAMES else ")")
                        for commit, eid, points, games in past[-HISTORY:])
    parts = []
    if flips:
        parts.append(f"flips {flips}, each a switch: a behaviour choice, ported as it is")
    if hints:
        parts.append(f"ports the behaviour behind the entry's moves off the champion's values, {hints}: each number "
                     "is a hint, not a value to set. Find what the "
                     "entry's value changes in its play (the decision it gates, where the entry's moves part from the "
                     "champion's) and build that as a rule over what the bot observes (map size, rounds left, "
                     "lengths, distances, counts, the opponent's behaviour; a param as its formula, 'P.x = <...>'), "
                     "the champion's number kept otherwise; a number set as the entry has it needs a Constants: "
                     "reason of loop/PROMPT.md's kinds in the commit")
    what = ("; it ".join(parts) + "; each part behind its own switch, nothing else changed") if parts \
        else "changes no value"
    if code:
        what += (f"; the champion has no {', '.join(change['missing'])}, which the entry's own code adds "
                 f"(git show {spec['commit'][:7]}): port that code behind its own switch ({code} in the entry), its "
                 "numbers as formulas of what the bot observes or with a Constants: reason")
    if change["same"]:
        what += f"; the champion already has the entry's {', '.join(change['same'])}"
    if change["swept"]:
        what += (". Left out, since the variant sweep of this champion measured each alone at least as well as the "
                 "entry scores (loop/sweep.py): " + "; ".join(change["swept"].values()))
    if change.get("unswept"):
        what += (". Kept although the sweep plays it alone, since a variant the sweep doesn't queue (under "
                 "sweep.QUEUE_AT over its self-play games, about +40 Elo) can still be worth a smaller gain: "
                 + "; ".join(change["unswept"].values()))
    first = next(iter(change["port"] or change["missing"]))
    return {
        "category": category(first, current_text),
        "priority": PRIORITY,
        "title": (f"Port the {spec['name']} screen entry's mechanism onto the champion, each part behind its own "
                  f"switch, from its moves off the champion's values ({moves or code}): it scores {score['score']:.2f} against {tag(current)} over {score['games']} "
                  "games"),
        "hypothesis": (
            f"Screen port (loop/screenport.py, method note M24). The `{spec['name']}` opponent-screen entry "
            f"(referee/gauntlet.toml: {spec['commit'][:7]}, built on {tag(base)}"
            + (f", with {overrides}" if overrides else "") + f") is {current - base} promotions older than the "
            f"current champion {tag(current)} ({champion[:7]}), yet it scored {score['score']:.3f} against it over "
            f"{score['games']} games ({score['source']}), where an entry that far behind usually loses clearly. Its "
            f"points in each earlier champion's latest screen (100 games each unless stated): {earlier or 'none'}. "
            f"So what its values do may be worth having in today's bot: this idea {what}. Compare each param's "
            f"comment in {tag(base)}'s and today's params.hpp first: where its meaning changed since, port the "
            "behaviour the entry's value had, not the number. A level score says the entry keeps up despite the "
            "promotions it lacks, not that its values gain: test it alone by SPRT as usual. If the SPRT's "
            "per-opponent scores pull apart, make a part depend on something the bot can observe rather than a new "
            "constant; if it is kept, a follow-up can test which part carries it."),
    }


# The switches an entry's params.hpp (`entry_text`) adds that its base champion's (`base_text`) lacks, at the entry's
# values (its gauntlet `params` overrides win: replayer turns its three on there): {name: value}; {} unless the entry
# extends that base, keeping every one of its param names (loneking, built from scratch on v0001's tag, keeps none).
def new_switches(entry_text: str, base_text: str, overrides: dict | None = None) -> dict:
    import sweep
    base = {p["name"] for p in sweep.read_params(base_text)}
    entry = sweep.read_params(entry_text)
    if not base or not base <= {p["name"] for p in entry}:
        return {}
    return {p["name"]: (overrides or {}).get(p["name"], p["value"]) for p in entry
            if p["kind"] == "bool" and p["name"] not in base}


# The code-switch route's decision for an entry adding `switches` (module docstring): its idea (a dict) to queue, or
# the text of why not. `score_on(commit)` is the entry's score against another champion (covered's).
def code_port(spec: dict, switches: dict, score: dict, base: int | None, current: int | None, champion: str,
              current_text: str, ideas: list[dict], rows: list[dict], score_on) -> dict | str:
    import sweep
    text = f"scored {score['score']:.2f} over {score['games']} games ({score['source']})"
    have = {p["name"]: p["value"] for p in sweep.read_params(current_text)}
    todo = {name: value for name, value in switches.items() if have.get(name) != value}
    shown = ", ".join(f"{n} = {literal(v)}" for n, v in switches.items())
    if not todo:
        return f"{text}; code switch {shown}: the champion already has it"
    hits = round(score["score"] * score["games"])
    p = chance.sign_p(hits, score["games"] - hits)
    if score["score"] <= 0.5 or p > chance.TRIGGER_P:
        return f"{text}; code switch {shown}: not above half at p <= {chance.TRIGGER_P} (p = {p:.3f})"
    if why := covered(ideas, rows, spec["name"], champion, todo, score["score"], score_on):
        return f"{text}; code switch {shown}: {why}"
    moves = ", ".join(f"{n} = {literal(v)}" for n, v in todo.items())
    diff = f"git diff {tag(base) if base else 'its base'}..{spec['commit'][:7]} -- bot/"
    idea = {"category": category(next(iter(todo)), current_text), "priority": 1,
            "title": (f"Port the {spec['name']} screen entry's code onto the champion behind its switch: {moves}: it "
                      f"scores {score['score']:.2f} against {tag(current) if current else champion[:7]} over "
                      f"{score['games']} games"),
            "hypothesis": (
                f"Screen port of code (loop/screenport.py, code-switch route). The `{spec['name']}` opponent-screen "
                f"entry (referee/gauntlet.toml: {spec['commit'][:7]}" + (f", built on {tag(base)}" if base else "")
                + f") adds {', '.join(todo)}, which the current champion {champion[:7]} lacks or has at another value, "
                f"and it scored {score['score']:.3f} against that champion over {score['games']} games "
                f"({score['source']}; luck alone gives a score this high p = {p:.3f}). So its code may be worth "
                f"having: port it ({diff}) onto today's bot behind {', '.join(todo)}, set to the entry's values "
                f"({moves}), adapting it where the champion's code changed since, and test it alone by SPRT as usual. "
                "A number the diff adds or moves keeps the champion's value or becomes a formula of what the bot "
                "observes, else its commit gives a Constants: reason. "
                "If the SPRT's per-opponent scores pull apart, make the switch depend on something the bot can "
                "observe rather than a constant.")}
    valid = meta.valid_proposals([json.dumps(idea)], 1, options=False)
    if not valid:
        return f"{text}; code switch {shown}: its idea failed validation"
    return {**valid[0], "next": True,
            "screen_port": {"entry": spec["name"], "champion": champion, "score": score["score"],
                            "games": score["games"], "values": todo, "code": True}}


# ------------------------------------------------------------------------------------------------- deciding

def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


# The decision for every gauntlet entry on today's data: [{"entry", "decision", "idea"?}], where "idea" is the
# proposal to queue (only when decision is "queue"). With `queue`, appends those ideas to the backlog under its lock
# (validated like the sweep's) and returns the decisions with their new ids; without, it takes no lock and writes
# nothing.
def decide(repo: Path, state: Path, queue: bool = False) -> list[dict]:
    import rescreen
    import sweep
    champion = meta.champion_commit(repo)
    if not champion:
        return []
    rows = meta.latest_rows(read_jsonl(state / "results.jsonl"))
    rescreens = read_json(state / "rescreen.json")
    import driver
    keys = {spec["name"]: driver.spec_key(spec) for spec in rescreen.gauntlet(repo)}
    scores = entry_scores(rows, rescreens, champion, state, keys)
    current = version(repo, champion)
    current_text = git(repo, "show", f"{champion}:bot/params.hpp")
    swept_scores = sweep_scores(state, champion, current_text)
    decisions = []
    # Read, decide and append as one step when queueing, so no claim or review interleaves.
    with meta.locked(state / "ideas.jsonl") if queue else contextlib.nullcontext():
        ideas = read_jsonl(state / "ideas.jsonl")
        for spec in rescreen.gauntlet(repo):
            name = spec["name"]
            decisions.append(out := {"entry": name})
            entry_text = git(repo, "show", f"{spec['commit']}:bot/params.hpp")
            base = version(repo, spec["commit"])
            base_text = git(repo, "show", f"{tag(base)}:bot/params.hpp") if base else ""
            def score_on(commit: str, name: str = name) -> float | None:
                return (entry_scores(rows, rescreens, commit, state, keys).get(name) or {}).get("score")

            switches = new_switches(entry_text, base_text, spec.get("params") or {})
            found = code_port(spec, switches, scores[name], base, current, champion, current_text, ideas, rows,
                              score_on) if switches and name in scores else None
            if isinstance(found, dict):  # else the params route below, like any other entry
                out.update(decision="queue", idea=found)
                if queue:
                    source = f"screen-port:{name}:{champion[:7]}"
                    ids = meta.append_ideas_locked(state, [found], source)
                    ideas.append({**found, "id": ids[0], "status": "todo", "source": source})
                    out["decision"] = f"code switch; queued as {ids[0]}"
                continue
            if spec.get("port") is False:  # an entry that isn't ours or is handicapped on purpose (greedyram, boxed)
                out["decision"] = "its entry says port = false" + (f"; {found}" if found else "")
                continue
            if not sweep.read_params(entry_text):
                out["decision"] = "its bot has no params to port (not ours: the toolkit's starter)"
                continue
            score = scores.get(name)
            if score is None:
                out["decision"] = "no screen of the current champion against it yet"
                continue
            text = f"scored {score['score']:.2f} over {score['games']} games ({score['source']})"
            if base is None or current is None:
                out["decision"] = f"{text}; no champion version for its commit or the champion's"
                continue
            if current - base < MIN_PROMOTIONS:
                out["decision"] = (f"{text}; built on {tag(base)}, {current - base} promotions old, under "
                                   f"{MIN_PROMOTIONS}")
                continue
            change = port(spec.get("params") or {}, entry_text, base_text, current_text)
            if not change["port"] and not change["missing"] and not change["same"]:
                out["decision"] = f"{text}; no param diff from its base {tag(base)}"
                continue
            if score["games"] < MIN_GAMES or score["score"] < PORT_AT:
                out["decision"] = f"{text}; under {PORT_AT} over {MIN_GAMES}+ games"
                continue
            change = drop_swept(change, current_text, swept_scores, score["score"])
            if not change["port"] and not change["missing"]:
                left = "; ".join(change["swept"].values()) or "none"
                out["decision"] = (f"{text}; nothing left to port: the champion already has "
                                   f"{', '.join(change['same']) or 'none'} and the sweep measures the rest ({left})")
                continue
            if why := covered(ideas, rows, name, champion, values(change), score["score"], score_on) \
                    or waits_on(change, ideas, name):
                out["decision"] = f"{text}; {why}"
                continue
            idea = proposal(spec, change, score, history(rows, name), base, current, champion, current_text)
            valid = meta.valid_proposals([json.dumps(idea)], 1, options=False)
            if not valid:
                out["decision"] = f"{text}; its idea failed validation"
                continue
            row = {**valid[0], "screen_port": {"entry": name, "champion": champion, "score": score["score"],
                                               "games": score["games"], "values": values(change)}}
            out.update(decision="queue", idea=row)
            if queue:
                source = f"screen-port:{name}:{champion[:7]}"
                ids = meta.append_ideas_locked(state, [row], source)
                ideas.append({**row, "id": ids[0], "status": "todo", "source": source})
                out["decision"] = f"{text}; queued as {ids[0]}"
    return decisions


_seen: tuple = ()


# The upkeep tick's step: decides and queues when its inputs changed since the last call (rescreen.inputs:
# results.jsonl, the champion, main's gauntlet.toml, rescreen.json; and baselines.json), and records the run in
# screenport.json: when it ran, each decision, and an error when it raised or an idea failed validation (health.py
# alerts on it). The change check advances either way, so a failing run isn't repeated every minute over
# results.jsonl. Returns a line per idea queued.
def tick(repo: Path, state: Path, now: float | None = None) -> list[str]:
    global _seen
    import online
    import rescreen
    if not (state / "ideas.jsonl").exists():
        return []
    # And baselines.json, which the prewarm rewrites as it plays the champion's screen games into its shared set.
    baselines = state / "baselines.json"
    key = (str(state), rescreen.inputs(state, repo), baselines.stat().st_mtime_ns if baselines.exists() else 0)
    if key == _seen:
        return []
    _seen = key
    now = time.time() if now is None else now
    record = read_json(state / RECORD)
    try:
        decisions = decide(repo, state, queue=True)
        failed = [f"{d['entry']}: {d['decision']}" for d in decisions if "failed validation" in d["decision"]]
        error = "; ".join(failed) or None
    except Exception as exc:
        decisions, error = [], f"{type(exc).__name__}: {exc}"[-600:]
    record.update(checked_at=now, error=error, decisions={d["entry"]: d["decision"] for d in decisions})
    if error:
        record["failed_at"] = now
    online.save(state / RECORD, record)
    return [f"screen port: {d['entry']} {d['decision']}" for d in decisions if "queued as" in d["decision"]]


def main() -> None:
    from home import loop_home
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    checking = commands.add_parser("check", help="print what it decides today, queueing and writing nothing")
    checking.add_argument("--state", type=Path, default=loop_home())
    args = parser.parse_args()
    for d in decide(REPO, args.state.resolve()):
        print(f"{d['entry']}: {d['decision']}")
        if "idea" in d:
            print(json.dumps(d["idea"], indent=1))


if __name__ == "__main__":
    sys.exit(main())
