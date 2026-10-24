"""A local web page that shows the loop's queue live (sig, 2026-09-29: "maybe it can be as simple as reading the json
file or whatever every 10 seconds when the html page is up", and at 18:18: "i want no extra work to happen when i
dont have the page up"):

    .venv/bin/python tools/queue_page.py [--port 8765]     # then open the URL it prints

It does work only while the page is open: nothing in the upkeep builds it. It runs until stopped (Ctrl-C), at about
0% CPU while no page polls and about 60-66 MB, and serves the loop code it started with, so restart it after loop
changes. It serves on 127.0.0.1 only and answers only a Host of 127.0.0.1:<port> or localhost:<port> (a page on
another site can't reach it by pointing a DNS name at 127.0.0.1).
GET / is one self-contained page that fetches /queue.json every 10 s and re-renders it; GET /queue.json is built
from the loop home's files (home.loop_home) when asked, read-only (tools/test_queue_page.py checks no file changes),
and kept CACHE_S, a failed build too, so several open tabs cost one build. It holds: the status line (evalline.line,
without its one writing part), the evaluation under way, part-time hosts that are down (evalline.part_time_down),
the experiments a stop marker ended with their candidate kept (`stopped`), the evaluation queue in the order the lock takes it (<loop home>/evaluation-queue tickets by file name: rank first,
so a next or same-moves candidate goes ahead, then oldest coding-done, driver.enter_queue), the ideas being coded, what the next supervised `--once` claim takes and the
rule that picks it (driver.claim_candidates for driver.claim_idea(0): only re-evaluations or tunes while every coding
slot is full; else "next" ideas and re-evaluations first, then fillers while nothing is queued or evaluating, then
goal-aimed ideas while sig's goal is short in flight, driver.goal_short, then driver.claim_order), the priority-1 ideas in the order a lane's
claim takes them ("next" and re-evaluations first, then driver.claim_order), the priority-1 ideas held back and why
(driver.held_back), waiting-idea counts by priority and the last lines of host-alerts.log. The backlog is read
without its lock; a half-written read serves the last good payload marked stale.
The page also fetches GET /history.json on load and once a minute and draws it as an inline SVG chart (sig, 29 Sep:
"a graph that shows how many items in the queue over time"), filtered to All or one of P1-P5: the waiting ideas
(todo and followup) above; the candidates queued for evaluation and the experiments being coded below, over bands
where an evaluation ran. Its history is the utilisation record the upkeep writes every minute anyway (throughput.LOG,
the last 24 h: throughput.CAP rows), so it covers the time the page was closed at no extra cost; rows from before a
field was recorded (29 Sep) show as gaps. /history.json is those rows as at most HISTORY_POINTS points (`point`),
rebuilt only when the record changes."""

from __future__ import annotations

import argparse
import html
import json
import math
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "loop"))

import driver  # noqa: E402
import evalline  # noqa: E402
import meta  # noqa: E402
import throughput  # noqa: E402
from home import evaluating_marker, loop_home  # noqa: E402

CACHE_S = 5  # seconds a built payload, or a failed build's stale one, is served again
NEXT_ROWS = 5  # ideas shown for the next supervised claim
ALERT_LINES = 10
ALERT_BYTES = 16384  # read from the end of host-alerts.log for its last lines
HISTORY_POINTS = 500  # at most this many points in /history.json: a day's 1,440 minute rows, every third one
PRIORITIES = (1, 2, 3, 4, 5)  # the chart's filters besides All


def read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def idea_row(idea: dict) -> dict:
    flags = [name for name, on in (("next", idea.get("next")), ("re-evaluate", idea.get("reevaluate")),
                                   ("filler", driver.is_filler(idea))) if on]
    return {"id": idea["id"], "priority": idea["priority"], "kind": idea.get("kind") or "", "rank": idea.get("rank"),
            "category": idea.get("category") or "", "flags": flags, "title": str(idea.get("title") or "")}


# The "idea" part of an experiment.json, parsed again only when the file changes: a recorded run's file is up to
# 4 MB and never changes, so each is parsed once per server, not every CACHE_S.
_ideas_of: dict[Path, tuple[int, dict]] = {}


def idea_of(run: Path) -> dict:
    path = run / "experiment.json"
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        return {}
    if (cached := _ideas_of.get(path)) and cached[0] == stamp:
        return cached[1]
    idea = read(path).get("idea")
    idea = idea if isinstance(idea, dict) else {}
    _ideas_of[path] = (stamp, idea)
    return idea


def experiment(state: Path, exp_id: str) -> dict:
    idea = idea_of(state / "runs" / exp_id)
    return {"experiment": exp_id, "idea": idea.get("id") or "?", "kind": idea.get("kind") or "",
            "title": str(idea.get("title") or "")}


def evaluating(state: Path, now: float) -> dict | None:
    marker = read(evaluating_marker())
    current = throughput.evaluation_now(state, now)
    if current is None:
        return None
    exp_id = current.get("experiment")
    row = experiment(state, exp_id) if exp_id else {"experiment": None, "idea": "", "kind": "", "title": ""}
    run = state / "runs" / (exp_id or "-")
    progress = read(run / "sprt-progress.json")
    return {**row, "stage": current.get("stage") or "setup", "since": marker.get("since"),
            "trials": progress.get("trials"), "max_pairs": read(run / "evaluation.json").get("max_pairs")}


# The tickets waiting for the evaluation lock in the order it takes them (rank first, next candidates ahead, then
# oldest coding-done: driver.enter_queue), each with whether the process that wrote it still runs.
def queue(state: Path) -> list[dict]:
    rows = []
    for ticket in sorted((state / meta.QUEUE).glob("*.json")):
        coded, exp_id = ["", *ticket.stem.split("-")][-2:]  # <rank>-<coded>-E#### (driver.enter_queue), or <coded>-E####
        body = read(ticket)
        try:
            coded_at = float(coded)
        except ValueError:
            coded_at = None
        rows.append({**experiment(state, body.get("experiment") or exp_id), "coded_at": coded_at,
                     "live": meta.alive(body.get("pid"))})  # not meta.ticket_live: its ps call per ticket costs 20 ms
    return rows


# Experiments a stop marker ended with their candidate kept (driver: verdict "blocked", "...; candidate preserved"),
# newest first, from run folders changed in the last STOPPED_DAYS: they hold no queue ticket while the loop is
# stopped and go back to evaluation by hand (driver.py --handed). Each experiment.json is parsed again only when it
# changes (_stopped_of), as idea_of does.
STOPPED_DAYS = 3
_stopped_of: dict[Path, tuple[int, dict | None]] = {}


def stopped(state: Path, now: float, ideas: list[dict]) -> list[dict]:
    status = {i["id"]: i["status"] for i in ideas}
    rows = []
    for run in (state / "runs").glob("E*"):
        path = run / "experiment.json"
        try:
            stamp = path.stat().st_mtime_ns
        except OSError:
            continue
        if now - stamp / 1e9 > STOPPED_DAYS * 86400:
            continue
        if not ((cached := _stopped_of.get(path)) and cached[0] == stamp):
            body = read(path)
            reason = str(body.get("reason") or "")
            row = None
            if body.get("verdict") == "blocked" and "candidate preserved" in reason:
                idea = body.get("idea") if isinstance(body.get("idea"), dict) else {}
                row = {"experiment": run.name, "idea": idea.get("id") or "?", "kind": idea.get("kind") or "",
                       "why": reason, "at": body.get("finished"), "title": str(idea.get("title") or "")}
            _stopped_of[path] = cached = (stamp, row)
        if cached[1]:
            rows.append({**cached[1], "idea_status": status.get(cached[1]["idea"], "?")})
    return sorted(rows, key=lambda r: -(r["at"] or 0))


# The ideas being tried whose experiment is neither queued nor evaluating: each with its lane and newest run (run
# folders newest first; one its history already records is an earlier try, so the new one is still being set up).
def coding(state: Path, ideas: list[dict], skip: set[str]) -> list[dict]:
    trying = {i["id"]: i for i in ideas if i["status"].startswith("trying:")}
    runs = {}
    for folder in sorted((state / "runs").glob("E*"), key=meta.run_number, reverse=True):
        if runs.keys() >= trying.keys():
            break
        idea = idea_of(folder).get("id")
        if idea in trying and idea not in runs:
            runs[idea] = folder.name
    rows = []
    for idea_id, idea in trying.items():
        exp_id = runs.get(idea_id)
        if exp_id and any(str(line).startswith(f"{exp_id}:") for line in idea.get("history") or []):
            exp_id = None
        if exp_id in skip:
            continue
        worker = read(state / "runs" / exp_id / "worker.json").get("status") if exp_id else None
        rows.append({**idea_row(idea), "lane": idea["status"].split(":", 1)[1], "experiment": exp_id,
                     "worker": worker or "setting up"})
    return sorted(rows, key=lambda r: r["experiment"] or "")


# Claim order as driver.claim_idea sorts a lane's claim (driver.claim_sequence): ready ideas, "next" and
# re-evaluations first (any priority), then driver.claim_order, a prerequisite as its waiters (driver.waiter_ranks);
# kept to those and priority 1. Then the priority-1 todo ideas held back, each with why (driver.held_back).
def claims(ideas: list[dict]) -> tuple[list[dict], list[dict]]:
    held, lifted = driver.held_back(ideas), driver.waiter_ranks(ideas)
    first = lambda i: bool(i.get("next") or i.get("reevaluate") or lifted.get(i["id"], {}).get("next"))
    order = [idea_row(i) for i in driver.claim_sequence(ideas) if first(i) or lifted.get(i["id"], i)["priority"] == 1]
    blocked = [{**idea_row(i), "after": i.get("after") or [], "why": "; ".join(why)}
               for i, why in sorted(held, key=lambda row: driver.claim_order(row[0])) if why and i["priority"] == 1]
    return order, blocked


RULES = {"full": "every coding slot is full (driver.CODING), so only a re-evaluation or a tune",
         "next": "an idea marked next, or a re-evaluation, goes first",
         "goal": "too few ideas in flight aim at sig's goal (driver.goal_short), so a goal-aimed idea goes first",
         "fillers": "nothing is queued or being evaluated, so fillers go first (tunes, retests and quick fixes at "
                    "priority 3 or better)",
         "order": "claim order: priority, then rank, then oldest"}


# What the next supervised `--once` claim (driver.claim_idea(0)) takes: its first NEXT_ROWS candidates and the rule
# that chose them, or why it would be refused.
def next_claim(ideas: list[dict]) -> dict:
    try:
        driver.free_lane(ideas, 0)
    except RuntimeError as error:
        return {"rule": "refused", "why": f"refused: {error}", "ideas": []}
    rule, todo = driver.claim_candidates(ideas, 0)
    return {"rule": rule, "why": RULES[rule], "ideas": [idea_row(i) for i in todo[:NEXT_ROWS]]}


# The part-time hosts down now (evalline.part_time_down), each with its outage's start and cores when known.
def outages(state: Path) -> list[dict]:
    if not (down := evalline.part_time_down(state)):
        return []
    cores = (throughput.last_row(state / throughput.LOG) or {}).get("cores") or {}
    return [{"host": name, "since": since, "cores": cores.get(name)} for name, since in sorted(down.items())]


def alerts(state: Path) -> list[str]:
    try:
        with (state / "host-alerts.log").open("rb") as handle:
            handle.seek(0, 2)
            handle.seek(max(0, handle.tell() - ALERT_BYTES))
            text = handle.read().decode(errors="replace")
    except OSError:
        return []
    return text.splitlines()[-ALERT_LINES:]


def build(state: Path, now: float) -> dict:
    ideas = throughput.read_ideas(state)
    try:
        line = evalline.line(state, now, evalline.gate_waits(), note_down=False)
    except Exception as error:  # the page still shows the rest
        line = f"evalline failed: {type(error).__name__}: {error}"
    current = evaluating(state, now)
    queued = queue(state)
    order, blocked = claims(ideas)
    skip = {r["experiment"] for r in queued} | ({current["experiment"]} if current else set())
    return {"generated": now, "stale": False, "error": None, "evalline": line, "evaluating": current,
            "outages": outages(state), "queue": queued, "coding": coding(state, ideas, skip),
            "stopped": stopped(state, now, ideas), "next_claim": next_claim(ideas), "claim_order": order, "blocked": blocked,
            "counts": throughput.backlog(ideas), "alerts": alerts(state)}


_last: dict = {}  # the payload served last, when it was built, and the last good one


# The payload to serve: the one built last while under CACHE_S old, else a new build; a build that fails (a backlog
# caught half written, a file gone mid-read) serves the last good one marked stale, with the error, and is kept
# CACHE_S like a good one, so a failing build runs once a CACHE_S however many tabs ask.
def payload(state: Path | None = None, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    if _last and now - _last["built"] < CACHE_S:
        return _last["payload"]
    started = time.perf_counter()
    try:
        body = build(state or loop_home(), now)
    except Exception as error:
        message = f"{type(error).__name__}: {error}"
        good = _last.get("good")
        body = {**good, "stale": True, "error": message} if good else {"generated": now, "stale": True,
                                                                        "error": message}
        _last.update(built=now, payload=body)
        return body
    body["took_ms"] = round((time.perf_counter() - started) * 1000)
    _last.update(built=now, payload=body, good=body)
    return body


# Experiments as [All, P1..P5], split by a row's "priorities" (None per priority for a row older than them).
def by_priority(experiments: list, priorities: object) -> list:
    if not isinstance(priorities, dict):
        return [len(experiments), *[None] * len(PRIORITIES)]
    return [len(experiments), *[sum(priorities.get(e) == p for e in experiments) for p in PRIORITIES]]


# One utilisation row as a chart point, [at, backlog, queued, coding, evaluating], each count [All, P1..P5]; backlog
# (waiting ideas: todo and followup) and coding are None for rows written before they were recorded (29 Sep).
def point(row: dict) -> list:
    backlog, coding, priorities = row.get("backlog"), row.get("coding"), row.get("priorities")
    ideas = None
    if isinstance(backlog, dict):
        waiting = {p: sum(c.values()) for p, c in backlog.items()}
        ideas = [sum(waiting.values()), *[waiting.get(str(p), 0) for p in PRIORITIES]]
    evaluation = row.get("evaluation")
    current = [evaluation.get("experiment")] if isinstance(evaluation, dict) else []
    return [round(row["at"]), ideas, by_priority(row.get("waiting") or [], priorities),
            by_priority(coding, priorities) if isinstance(coding, list) else None, by_priority(current, priorities)]


_history: dict = {}  # the last /history.json and the utilisation record's (mtime, size) it was built from


# The utilisation record (throughput.LOG, a minute's row each, the newest throughput.CAP kept, written by the upkeep
# whether or not the page is open) as at most HISTORY_POINTS points, newest kept: rebuilt only when the file changed
# (a minute), so several tabs cost one build. A failed build serves no points and its error.
def history(state: Path | None = None) -> dict:
    state = state or loop_home()
    try:
        info = (state / throughput.LOG).stat()
        stamp = (info.st_mtime_ns, info.st_size)
    except OSError:
        stamp = None
    if stamp and _history.get("stamp") == stamp:
        return _history["body"]
    started = time.perf_counter()
    try:
        rows = [r for r in throughput.read_rows(state) if isinstance(r.get("at"), (int, float))]
        every = max(1, math.ceil(len(rows) / HISTORY_POINTS))
        body = {"points": [point(r) for r in rows[::-1][::every][::-1]], "every": every, "error": None}
    except Exception as error:
        body = {"points": [], "every": 1, "error": f"{type(error).__name__}: {error}"}
    body["took_ms"] = round((time.perf_counter() - started) * 1000)
    _history.update(stamp=stamp, body=body)
    return body


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Loop Queue</title>
<style>
:root { --bg: #fbfbfa; --fg: #1d1d1b; --muted: #6b6b66; --line: #e2e1dc; --head: #f1f0ec; --bad: #b3261e;
  --warn: #8a5a00; --chip: #e8e6df; --c1: #2a78d6; --c2: #eb6834; --c3: #1baf7a; }
@media (prefers-color-scheme: dark) { :root { --bg: #161615; --fg: #e8e7e3; --muted: #9a9993; --line: #2e2d2a;
  --head: #1f1f1d; --bad: #f2b8b5; --warn: #e6c170; --chip: #2c2b28; --c1: #3987e5; --c2: #d95926; --c3: #199e70; } }
body { background: var(--bg); color: var(--fg); font: 14px/1.45 -apple-system, system-ui, sans-serif; margin: 0;
  padding: 16px 24px 40px; }
h1 { font-size: 18px; margin: 0 0 4px; } h2 { font-size: 15px; margin: 24px 0 6px; }
#status { color: var(--muted); margin-bottom: 8px; } #status.bad { color: var(--bad); font-weight: 600; }
#status.stale { color: var(--warn); font-weight: 600; }
pre { white-space: pre-wrap; background: var(--head); border: 1px solid var(--line); padding: 8px 10px;
  border-radius: 6px; margin: 0; font: 12.5px/1.45 ui-monospace, Menlo, monospace; }
table { border-collapse: collapse; width: 100%; max-width: 1400px; }
th, td { text-align: left; padding: 4px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { background: var(--head); font-weight: 600; white-space: nowrap; }
td.n { white-space: nowrap; font-variant-numeric: tabular-nums; }
.chip { background: var(--chip); border-radius: 4px; padding: 0 5px; margin-right: 3px; font-size: 12px; }
.empty { color: var(--muted); font-style: italic; } .dead { color: var(--bad); }
@media (max-width: 600px) { body { padding: 12px 16px 32px; } }
#filters button { font: inherit; font-size: 13px; color: var(--fg); background: var(--chip); border: 1px solid var(--line);
  border-radius: 4px; padding: 2px 10px; margin: 0 4px 6px 0; cursor: pointer; }
#filters button.on { background: var(--fg); color: var(--bg); border-color: var(--fg); }
#chart { max-width: 1400px; } #chart svg { display: block; max-width: 100%; touch-action: pan-y; }
#chart .grid { stroke: var(--line); } #chart .axis { fill: var(--muted); font-size: 11px; }
#chart .line { fill: none; stroke-width: 2; stroke-linejoin: round; } #chart .band { fill: var(--chip); }
#chart .cross { stroke: var(--muted); stroke-dasharray: 2 2; }
.legend { color: var(--muted); font-size: 12.5px; margin: 4px 0; } .legend span { margin-right: 14px; white-space: nowrap; }
.legend i { display: inline-block; width: 14px; height: 0; border-top: 2px solid; vertical-align: middle; margin-right: 5px; }
.legend i.box { height: 10px; border: 0; background: var(--chip); }
#readout { font-variant-numeric: tabular-nums; min-height: 1.45em; }
</style></head><body>
<h1>Loop queue</h1><div id="status">loading...</div>
<h2>Over the last 24 h (utilisation.jsonl's minute rows)</h2><div id="filters"></div><div id="chart"></div>
<div id="readout"></div><div id="root"></div>
<script>
const TITLE = 140;
const hhmm = t => typeof t === "number" ? new Date(t * 1000).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"}) : "";
function el(tag, text, cls) { const e = document.createElement(tag); if (text != null) e.textContent = text;
  if (cls) e.className = cls; return e; }
function title(text) { const td = el("td", text.length > TITLE ? text.slice(0, TITLE) + "..." : text);
  td.title = text; return td; }
function flags(list) { const td = el("td"); for (const f of list || []) td.append(el("span", f, "chip")); return td; }
function section(root, name, cols, rows, cells, empty) {
  root.append(el("h2", name + (rows.length ? " (" + rows.length + ")" : "")));
  if (!rows.length) { root.append(el("div", empty || "none", "empty")); return; }
  const table = el("table"), head = el("tr");
  for (const c of cols) head.append(el("th", c));
  table.append(head);
  for (const r of rows) { const tr = el("tr"); for (const c of cells(r)) tr.append(typeof c === "string" || c == null ?
    el("td", c ?? "", "n") : c); table.append(tr); }
  root.append(table);
}
function render(d) {
  const root = el("div");
  root.append(el("h2", "Status line")); root.append(el("pre", d.evalline || ""));
  const e = d.evaluating;
  section(root, "Being evaluated", ["experiment", "idea", "kind", "stage", "trials", "since", "title"], e ? [e] : [],
    r => [r.experiment || "(lock, no bot yet)", r.idea, r.kind, r.stage,
          r.trials != null ? r.trials + "/" + (r.max_pairs || "?") : "", hhmm(r.since), title(r.title)], "idle");
  section(root, "Part-time hosts down (only sig can restart them)", ["host", "since", "cores"], d.outages || [],
    r => [r.host, hhmm(r.since), r.cores != null ? String(r.cores) : ""]);
  section(root, "Evaluation queue, in the order the lock takes it", ["#", "experiment", "idea", "kind", "coded", "title"],
    d.queue || [], r => [String(d.queue.indexOf(r) + 1), r.experiment, r.idea, r.kind,
    hhmm(r.coded_at) + (r.live ? "" : " (ticket's process gone)"), title(r.title)]);
  section(root, "Stopped with the candidate kept (back to evaluation with driver.py --handed)",
    ["experiment", "idea", "idea status", "kind", "stopped", "why", "title"], d.stopped || [],
    r => [r.experiment, r.idea, r.idea_status, r.kind, hhmm(r.at), r.why, title(r.title)]);
  section(root, "Being coded", ["experiment", "idea", "lane", "worker", "kind", "title"], d.coding || [],
    r => [r.experiment || "-", r.id, r.lane, r.worker, r.kind, title(r.title)]);
  const n = d.next_claim || {ideas: []};
  section(root, "Next supervised claim (driver.claim_idea(0)): " + (n.why || ""), ["#", "idea", "P", "kind", "flags", "title"],
    n.ideas || [], r => [String(n.ideas.indexOf(r) + 1), r.id, String(r.priority), r.kind, flags(r.flags), title(r.title)],
    n.rule === "refused" ? "no claim" : "nothing to claim");
  section(root, "A lane's claim order: next and re-evaluations first, then priority 1",
    ["#", "idea", "P", "kind", "rank", "flags", "title"], d.claim_order || [],
    r => [String(d.claim_order.indexOf(r) + 1), r.id, String(r.priority), r.kind, String(r.rank ?? ""), flags(r.flags), title(r.title)]);
  section(root, "Priority 1 held back", ["idea", "kind", "after", "why", "flags", "title"],
    d.blocked || [], r => [r.id, r.kind, (r.after || []).join(", "), r.why || "", flags(r.flags), title(r.title)]);
  const counts = Object.entries(d.counts || {});
  section(root, "Waiting ideas by priority", ["priority", "todo", "followup"], counts,
    ([p, c]) => [p, String(c.todo), String(c.followup)]);
  root.append(el("h2", "host-alerts.log, last lines")); root.append(el("pre", (d.alerts || []).join("\\n") || "(none)"));
  document.getElementById("root").replaceWith(root); root.id = "root";
}
// The history chart: /history.json's points [at, backlog, queued, coding, evaluating], each [All, P1..P5] or null
// (a row older than that field: drawn as a gap). Two panels share the time axis, since the backlog runs to hundreds
// and the pipeline counts to a few: waiting ideas on top; queued and coding below, over bands where an evaluation ran.
const FILTERS = ["All", "P1", "P2", "P3", "P4", "P5"];
const SERIES = [{i: 1, name: "waiting ideas (todo + followup)", color: "var(--c1)", panel: 0},
                {i: 2, name: "queued for evaluation", color: "var(--c2)", panel: 1},
                {i: 3, name: "being coded", color: "var(--c3)", panel: 1, dash: "6 3"}];
let hist = null, filter = 0;
try { filter = Math.max(0, FILTERS.indexOf(localStorage.getItem("queueFilter"))); } catch (e) {}
const NS = "http://www.w3.org/2000/svg";
function sv(tag, attrs, text) { const e = document.createElementNS(NS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]); if (text != null) e.textContent = text; return e; }
function niceMax(m) { if (m <= 2) return 2; const p = Math.pow(10, Math.floor(Math.log10(m)));
  for (const k of [1, 2, 4, 6, 8, 10]) if (k * p >= m) return k * p; }  // halves stay whole counts
const value = (pt, i) => pt[i] ? pt[i][filter] : null;
function drawFilters() {
  const box = document.getElementById("filters"); box.replaceChildren();
  FILTERS.forEach((name, i) => { const b = el("button", name, i === filter ? "on" : ""); b.type = "button";
    b.onclick = () => { filter = i; try { localStorage.setItem("queueFilter", name); } catch (e) {} drawFilters(); drawChart(); };
    box.append(b); });
}
function drawChart() {
  const box = document.getElementById("chart"), readout = document.getElementById("readout");
  box.replaceChildren(); readout.textContent = "";
  const pts = hist && hist.points || [];
  if (!pts.length) { box.append(el("div", hist && hist.error ? "history failed: " + hist.error : "no history yet", "empty")); return; }
  const W = Math.max(280, Math.min(box.clientWidth || 800, 1400)), L = 36, R = 8, T = 6, B = 20, GAP = 30, PH = [120, 80];
  const tops = [T, T + PH[0] + GAP], H = tops[1] + PH[1] + B;
  const x0 = pts[0][0], x1 = Math.max(pts[pts.length - 1][0], x0 + 60);
  const X = t => L + (t - x0) / (x1 - x0) * (W - L - R);
  const brk = Math.max(180, 180 * (hist.every || 1));  // points further apart: the sampler was down, so the line breaks
  const svg = sv("svg", {viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img",
    "aria-label": "Waiting ideas, queued and coding experiments over time"});
  [0, 1].forEach(panel => {
    const series = SERIES.filter(s => s.panel === panel);
    let most = 0, any = false;
    for (const pt of pts) for (const s of series) { const v = value(pt, s.i); if (v != null) { any = true; most = Math.max(most, v); } }
    const top = niceMax(most), Y = v => tops[panel] + PH[panel] - v / top * PH[panel];
    if (!any) svg.append(sv("text", {x: (L + W - R) / 2, y: tops[panel] + PH[panel] / 2, "text-anchor": "middle", class: "axis"},
      "not recorded in these rows yet"));
    if (panel === 1) pts.forEach((pt, n) => { if (value(pt, 4)) { const a = X(pt[0]),
      b = n + 1 < pts.length && pts[n + 1][0] - pt[0] <= brk ? X(pts[n + 1][0]) : a + 1;
      svg.append(sv("rect", {x: a, y: tops[1], width: Math.max(1, b - a), height: PH[1], class: "band"})); } });
    for (const v of [0, top / 2, top]) { svg.append(sv("line", {x1: L, x2: W - R, y1: Y(v), y2: Y(v), class: "grid"}));
      svg.append(sv("text", {x: L - 5, y: Y(v) + 4, "text-anchor": "end", class: "axis"}, String(+v.toFixed(1)))); }
    for (const s of series) { let d = "", prev = null;
      for (const pt of pts) { const v = value(pt, s.i);
        if (v == null) { prev = null; continue; }
        d += (prev && pt[0] - prev[0] <= brk ? "L" : "M") + X(pt[0]).toFixed(1) + " " + Y(v).toFixed(1); prev = pt; }
      if (d) svg.append(sv("path", {d, class: "line", stroke: s.color, ...(s.dash ? {"stroke-dasharray": s.dash} : {})})); }
  });
  const hours = (x1 - x0) / 3600, step = [1, 2, 3, 6, 12, 24].find(h => hours / h <= (W - L - R) / 60) || 24;
  for (let t = Math.ceil(x0 / 3600) * 3600; t <= x1; t += 3600)
    if (new Date(t * 1000).getHours() % step === 0 && X(t) > L + 24 && X(t) < W - R - 30)
    svg.append(sv("text", {x: X(t), y: H - 5, "text-anchor": "middle", class: "axis"}, hhmm(t)));
  const cross = sv("line", {y1: T, y2: H - B, class: "cross", visibility: "hidden"}); svg.append(cross);
  const hit = sv("rect", {x: L, y: 0, width: W - L - R, height: H, fill: "transparent"}); svg.append(hit);
  const show = event => { const r = svg.getBoundingClientRect(), x = (event.clientX - r.left) * W / r.width;
    const t = x0 + (x - L) / (W - L - R) * (x1 - x0);
    const pt = pts.reduce((a, b) => Math.abs(b[0] - t) < Math.abs(a[0] - t) ? b : a);
    cross.setAttribute("x1", X(pt[0])); cross.setAttribute("x2", X(pt[0])); cross.setAttribute("visibility", "visible");
    const n = i => value(pt, i) ?? "?";
    readout.textContent = FILTERS[filter] + " at " + hhmm(pt[0]) + ": " + n(1) + " waiting ideas, " + n(2) + " queued, " +
      n(3) + " being coded" + (value(pt, 4) ? ", evaluating" : value(pt, 4) == null ? ", evaluation ?" : ""); };
  hit.addEventListener("pointermove", show); hit.addEventListener("pointerdown", show);
  hit.addEventListener("pointerleave", () => { cross.setAttribute("visibility", "hidden"); readout.textContent = ""; });
  box.append(svg);
  const legend = el("div", null, "legend");
  for (const s of SERIES) { const span = el("span"), key = el("i"); key.style.borderColor = s.color;
    if (s.dash) key.style.borderTopStyle = "dashed"; span.append(key, s.name); legend.append(span); }
  const band = el("span"); band.append(el("i", null, "box"), "an evaluation running (lower panel)"); legend.append(band);
  box.append(legend, el("div", "a point every " + hist.every + " min, the newest last; a gap: not recorded then", "legend"));
}
async function loadHistory() {
  try { const r = await fetch("/history.json", {cache: "no-store"}); if (!r.ok) throw new Error("HTTP " + r.status);
        hist = await r.json(); }
  catch (err) { hist = {...(hist || {points: []}), error: "fetch failed: " + err.message}; }
  drawChart();
}
async function tick() {
  const status = document.getElementById("status");
  try {
    const r = await fetch("/queue.json", {cache: "no-store"});
    if (!r.ok) throw new Error("HTTP " + r.status);
    const d = await r.json();
    if (d.evalline !== undefined) render(d);
    const built = "built " + new Date(d.generated * 1000).toLocaleTimeString() + (d.took_ms != null ? " in " + d.took_ms + " ms" : "");
    status.textContent = d.stale ? "STALE: showing the last good read (" + built + "): " + d.error
                                 : "updated " + new Date().toLocaleTimeString() + ", " + built + "; refreshes every 10 s";
    status.className = d.stale ? "stale" : "";
  } catch (err) {
    status.textContent = "FETCH FAILED at " + new Date().toLocaleTimeString() + ": " + err.message +
      " (is tools/queue_page.py still running?); the tables below are from the last good fetch";
    status.className = "bad";
  }
}
tick(); setInterval(tick, 10000);
drawFilters(); loadHistory(); setInterval(loadHistory, 60000); addEventListener("resize", drawChart);
</script></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        port = self.server.server_port
        if self.headers.get("Host") not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            self.send(b"forbidden host", "text/plain; charset=utf-8", 403)
            return
        if self.path == "/":
            self.send(PAGE.encode(), "text/html; charset=utf-8")
        elif self.path == "/queue.json":
            self.send(json.dumps(payload()).encode(), "application/json")
        elif self.path == "/history.json":
            self.send(json.dumps(history(), separators=(",", ":")).encode(), "application/json")
        else:
            self.send(html.escape(f"not found: {self.path}").encode(), "text/plain; charset=utf-8", 404)

    def send(self, body: bytes, kind: str, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # one line every 10 s per open tab is noise
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = HTTPServer(("127.0.0.1", args.port), Handler)
    print(f"http://127.0.0.1:{server.server_port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
