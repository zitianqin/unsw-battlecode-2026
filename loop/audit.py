"""Replay audits: AUDIT_EVERY_S after the last finished one (audit.json's `finished_at`; at once with none), a model
watches our games like a coach (loop/AUDIT.md), writes its findings and queues up to MAX_IDEAS experiments.

Both providers share prepare/apply. The Claude driver launches the worker in the background from its claim
step, so coding and games carry on meanwhile, and the upkeep tick applies it once the worker exits
(`apply_finished`), each in a fresh process of main's code (`.venv/bin/python loop/audit.py apply <loop home>
<audit folder>`, meta.fresh_line); native `claim` hands the task to the active model instead
(`audit_required`), which runs `native.py audited <folder>` when done. Findings live in `audits/<id>/findings.md`;
the latest is linked from `audits/latest.md` for reviews.
"""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import meta
import online
import quota

# sig, 2026-09-29: "replay reviews - i want both less often. the smaller one should fire every 12 hours ish. and the
# bigger one every 2 days ish" (the audit and the review, meta.META_EVERY_S; it was every 5 results).
AUDIT_EVERY_S = 12 * 3600
MAX_IDEAS = 5
RETRY_S = 3600  # after a failed worker (one a quota limit stopped: at its reset, meta.when_due)
REPO = Path(__file__).resolve().parent.parent
PROMPT = REPO / "loop/AUDIT.md"


def results_count(state: Path) -> int:
    path = state / "results.jsonl"
    return sum(1 for line in path.read_text().splitlines() if line.strip()) if path.exists() else 0


def read(state: Path) -> dict:
    return online.read(state / "audit.json", {})


def save(state: Path, record: dict) -> None:
    online.save(state / "audit.json", record)


def due(state: Path) -> bool:
    record = read(state)
    return not record.get("pending") and time.time() - record.get("finished_at", 0) >= AUDIT_EVERY_S


# Whether we won an online game: replay summaries say "A"/"B" (every audit until 2026-09-26 compared them with the
# side lowercased, so it listed every game as lost and couldn't put losses first).
def we_won(game: dict) -> bool:
    winner = game.get("summary", {}).get("result", {}).get("winner")
    return isinstance(winner, str) and winner.upper() == game["side"].upper()


# Who each lost SPRT game in run folder `run` was against, by map stem: trial i plays the i-th map against
# meta.trial_opponent (the rotation in turn, or an opponent mix's schedule), and sprt.json keeps per_map in trial
# order. Empty when sprt.json is missing.
def loss_opponents(run: Path) -> dict[str, str]:
    try:
        sprt = json.loads((run / "sprt.json").read_text())
    except (OSError, ValueError):
        return {}
    return {Path(m).stem: meta.trial_opponent(sprt, i) for i, m in enumerate(sprt.get("per_map") or {})}


DEV_LOSSES = 4  # the dev-team losses listed after the ladder games


# The newest of our current submission's dev losses and draws to watch (`dev_losses`, newest first), each with its
# kind: DEV_LOSSES // 2 to teams we beat heavily (online.json's dev.teams rows whose `lower`, the 95% lower bound of our
# score over the games that set its share, every version and pool period, online.dev_report, is at least
# online.DEV_HEAVY, the most surprising losses: a flaw their score hides; on 2026-09-27 we lost 15 of 1,012 games to
# them, 11 on Trauma), then
# the rest to the dev benchmark (`benchmark`, online.dev_benchmark: the one dev team near level with us), each kind
# filling from the other when it has fewer, then losses to the other dev teams.
def dev_picks(dev_losses: list[dict], teams: list[dict], benchmark: int | None) -> list[tuple[dict, str]]:
    heavy = {t["id"]: t["score"] for t in teams if (t.get("lower") or 0) >= online.DEV_HEAVY and t["id"] != benchmark}
    beaten = [(g, f"a team we beat {heavy[g['opponent']]:.0%} over every version and pool period")
              for g in dev_losses if g.get("opponent") in heavy]
    bench = [(g, "the dev benchmark") for g in dev_losses if benchmark and g.get("opponent") == benchmark]
    other = [(g, "") for g in dev_losses if g.get("opponent") not in heavy and g.get("opponent") != benchmark]
    first = beaten[:max(DEV_LOSSES // 2, DEV_LOSSES - len(bench))]
    second = bench[:DEV_LOSSES - len(first)]
    return first + second + other[:DEV_LOSSES - len(first) - len(second)]


# A study.md leader or dev title (maintenance.leader_sections, dev_section): "## Rank 1: Cutlery (team 306, formerly
# "Vibing++", Elo 2121)" or "## Dev benchmark: NAME (team 545, Elo 1960, dev), submission 9571". Everything after the
# name is matched to the line's end, older names as quoted_name's JSON strings, so a name anyone picks can't pass off
# another team's id: an old one carrying "(team 157, Elo 1)" sits inside its quotes, and a current one carrying it
# is followed by the real "(team N".
QUOTED = r'"(?:[^"\\]|\\.)*"'
LEADER_TITLE = re.compile(rf"## (Rank \d+|Dev benchmark): (.+) \(team (\d+)(?:, formerly {QUOTED}(?:, {QUOTED})*)?"
                          r", Elo [^,()]*(?:, dev)?\)(?:, submission \S+)?$")


# The games to watch, as (replay, our side or None, label): our current submission's play group's (online.same_play:
# the submissions that play as it) ladder and autoscrim games (losses first), then DEV_LOSSES of its losses and draws
# to the organisers' dev teams (dev_picks), labelled so (a few hundred dev games an hour would otherwise fill the list;
# online.against_dev). Then the latest promoted
# candidate's lost local test games, and the leaders' games from study.md. Online games whose replay is gone
# (prune.old_dev_replays) are left out.
def games(state: Path) -> list[tuple[Path, str | None, str]]:
    listed: list[tuple[Path, str | None, str]] = []
    report = online.read(state / "online.json", {})
    dev = online.dev_ids(report)
    ours = [g for g in report.get("games", []) if g.get("submission_id") in online.same_play(report)
            and Path(g.get("replay") or "").is_file()]
    ours.sort(key=lambda g: (we_won(g), -int(g["id"])))
    for g in [g for g in ours if not online.against_dev(g, dev)][:12]:
        won = we_won(g)
        listed.append((Path(g["replay"]), g["side"], f"online vs {g.get('opponent')}, {'won' if won else 'lost'}"))
    benchmark = ((report.get("benchmark") or {}).get("team") or {}).get("id")
    dev_losses = sorted((g for g in ours if online.against_dev(g, dev) and not we_won(g)), key=lambda g: -int(g["id"]))
    for g, which in dev_picks(dev_losses, (report.get("dev") or {}).get("teams", []), benchmark):
        result = "drew" if g.get("summary", {}).get("result", {}).get("winner") is None else "lost"
        why = "; ".join(filter(None, [which, "replay kept because we lost" if online.selected(g) else ""]))
        listed.append((Path(g["replay"]), g["side"],
                       f"online vs dev team {g.get('opponent')}{f' ({why})' if why else ''}, {result}"))
    kept = [json.loads(line) for line in (state / "results.jsonl").read_text().splitlines()
            if line.strip() and json.loads(line).get("verdict") == "kept"] if (state / "results.jsonl").exists() else []
    if kept:
        exp = kept[-1]["id"]
        opponents = loss_opponents(state / "runs" / exp)
        for path in sorted((state / "runs" / exp / "losses").glob("*.replay"))[:10]:
            side = "A" if path.stem.endswith("-aA") else "B"
            opponent = opponents.get(path.stem.split("-", 1)[-1].rsplit("-", 1)[0])  # 0003-<map stem>-aB
            against = ("the previous champion" if opponent == "champion" else
                       f"the opponent mix's {opponent} (a gauntlet entry)" if opponent in meta.MIX_OPPONENTS else
                       f"the rotation's {opponent} (a frozen variant)" if opponent else "the previous champion or a "
                       "rotation opponent (no sprt.json to tell)")
            listed.append((path, side, f"local test game lost by {exp} (now the champion) to {against}"))
    study = state / "study.md"
    if study.exists():
        index = {}
        for line in (state / "study-replays/index.jsonl").read_text().splitlines() if (state / "study-replays/index.jsonl").exists() else []:
            row = json.loads(line)
            index[str(row["match"])] = row
        team = None
        for line in study.read_text().splitlines():
            if m := LEADER_TITLE.match(line):
                name = m.group(2) if m.group(1).startswith("Rank") else f"dev benchmark {m.group(2)}"
                team = (name, int(m.group(3)))
            elif line.startswith("## "):
                team = None  # "Us", "Gaps unchanged": not a leader's games
            elif team and line.startswith("Replays to open"):
                for raw in line.split(": ", 1)[1].split(", "):
                    path = Path(raw.strip())
                    row = index.get(path.stem, {})
                    side = "A" if row.get("teamAId") == team[1] else "B" if row.get("teamBId") == team[1] else None
                    listed.append((path, side, f"leader {team[0]}"))
    return [g for g in listed if g[0].exists()]


BLIND_WATCH = 2  # online losses an audit watches blind first (AUDIT.md step 0)


# MISSES 64 level 3, the blind watch: BLIND_WATCH of our current submission's play group's (online.same_play) online
# losses and draws with a replay, ladder or dev, picked by `rng` (random, not the worst: the list games() gives is
# ordered by what we already count). The worker watches them turn by turn before reading any count or finding, then
# marks what it saw covered or not by the current findings; one no finding covers is an analyser miss.
def blind_picks(state: Path, rng: random.Random) -> list[tuple[Path, str, str]]:
    report = online.read(state / "online.json", {})
    dev = online.dev_ids(report)
    lost = [g for g in report.get("games", []) if g.get("submission_id") in online.same_play(report)
            and not we_won(g) and Path(g.get("replay") or "").is_file()]
    return [(Path(g["replay"]), g["side"], f"online vs {'dev team ' if online.against_dev(g, dev) else ''}"
             f"{g.get('opponent')}") for g in rng.sample(lost, min(BLIND_WATCH, len(lost)))]


# Which build the live submission is, from publishing.json's verified archive, so no one infers it from a request
# time (the 17:03 audit of 2026-09-26 read 7003's "06:33Z" as local time and called it an older build: it was v0030).
def live_build(state: Path) -> list[str]:
    verified = online.read(state / "publishing.json", {}).get("verified") or {}
    commit, submission = verified.get("commit"), verified.get("submission_id")
    if not (commit and submission):
        return []
    def tag_of(rev: str) -> str:
        tags = subprocess.run(["git", "tag", "--points-at", rev, "v*"], cwd=REPO, capture_output=True, text=True).stdout
        return " ".join(t for t in tags.split() if t[1:].isdigit()) or "no version tag"
    site = f", the site's version {verified['site_version']}" if verified.get("site_version") else ""
    same = online.play_group(state, submission)[1:]
    also = (f", and those of {', '.join(map(str, same))}, which build the same code but the sonar key "
            "(referee/samecode.py)" if same else "")
    lines = [f"Our live submission {submission} is commit {commit[:7]} ({tag_of(commit)}{site}); its online games in "
             f"the list below are that build's{also}."]
    # The champion can be ahead of the live build (an upload waits for the daily budget): findings from online games
    # then describe the older bot, and the champion code may already have changed what they blame.
    champion = subprocess.run(["git", "rev-parse", "loop/champion"], cwd=REPO, capture_output=True, text=True).stdout.strip()
    if champion and champion != commit:
        changes = subprocess.run(["git", "log", "--format=- %s", f"{commit}..{champion}", "--", "bot"], cwd=REPO,
                                 capture_output=True, text=True).stdout.strip()
        lines += [f"The champion code you read is newer: commit {champion[:7]} ({tag_of(champion)}), not yet live. Before "
                  "blaming an online loss on a rule, check the champion still has it; what changed since the live build:",
                  *[line[:200] for line in changes.splitlines()[:10]]]
    return lines


RECORD_VERSIONS = 4  # the newest versions of ours whose online records context.md gives


# context.md's online records (MISSES 55): the listed games are picked losses first and an index's `games` holds
# replays kept because we lost, so the worker gets each of the newest RECORD_VERSIONS versions' full record from every
# game (online.all_games), dev and ladder games apart (online.against_dev) and each dev team's losses of its games,
# beside what its replays are: the result-blind sample (online.sample_games) and the replays kept because we lost.
def record_lines(state: Path) -> list[str]:
    dev = online.dev_ids(online.read(state / "online.json", {}))
    folder = state / "online-replays"
    versions = sorted((int(p.parent.name) for p in folder.glob("*/index.json") if p.parent.name.isdigit()),
                      reverse=True)[:RECORD_VERSIONS]
    lines = []
    for version in versions:
        index = online.read(folder / str(version) / "index.json", {})
        every = online.all_games(index)
        for kind, rows in (("dev teams", [g for g in every if online.against_dev(g, dev)]),
                           ("ladder and autoscrim", [g for g in every if not online.against_dev(g, dev)])):
            if not rows:
                continue
            ids = {g["id"] for g in rows}
            saved = [g for g in index.get("games", []) if g["id"] in ids]
            sample, chosen = online.sample_games(saved), [g for g in saved if online.selected(g)]
            whole, part = online.record(rows), online.record(sample) if sample else None
            line = (f"- {version} vs {kind}: every game {whole['games']}, won {whole['wins']}, drew {whole['draws']}, "
                    f"lost {whole['losses']} ({whole['losses'] / whole['games']:.1%}); replays: "
                    + (f"{part['games']} result-blind (lost {part['losses']})" if part else "none result-blind")
                    + (f", {len(chosen)} kept because we lost (for watching only)" if chosen else "")
                    + ("; its dev wins' replay files are pruned, so its files are no sample (online.file_selected)"
                       if any(g.get("dev_wins_pruned") for g in saved) else ""))
            if kind == "dev teams":
                teams = sorted({g.get("opponent") for g in rows}, key=lambda t: t or 0)
                line += "; lost/games by team: " + ", ".join(
                    f"{t} {sum(online.game_score(g) == 0 for g in rows if g.get('opponent') == t)}/"
                    f"{sum(g.get('opponent') == t for g in rows)}" for t in teams)
            lines.append(line)
    if not lines:
        return []
    return ["", "Our online records (every game: each online-replays/<version>/index.json's `results` and `games` "
            "together, online.all_games). A rate, record or per-map or per-opponent comparison of our online games "
            "comes from every game, never from `games` alone: its replays marked `\"kept\": \"loss\"` were kept "
            "because we lost, so a rate or mean needing replay summaries uses the unmarked ones "
            "(online.sample_games). runs/E####/losses holds only lost games (their sprt.json has every game).",
            *lines]


def context(state: Path, rng: random.Random | None = None) -> str:
    python = REPO / ".venv/bin/python"
    lines = [f"Watch tools: {python} {REPO / 'tools/watch.py'} ...   and   {python} {REPO / 'tools/replays.py'} ...",
             f"Blunder finder: {python} {REPO / 'tools/blunders.py'} FILES... --team A|B [--worst N] [--json OUT]",
             f"Champion code: {state / 'champion/bot'} (main program, then the headers it includes).",
             f"Game rules: {REPO / 'docs/unsw/README.md'}. How the leaders play and their gaps with us: study.md in "
             "this folder. Earlier audit findings: earlier-findings.md in this folder, when there is one.",
             *live_build(state), *record_lines(state), "",
             "Games (replay, our side, what it is), chosen losses first to watch, never a sample to count rates from:"]
    watched = set(read(state).get("leaders_watched", []))
    listed = games(state)
    bceval_read = online.bceval_module()
    if bceval_read.enabled():
        lines.insert(-1, "Each game with a side ends with bceval's turning point for that side (tools/bceval_read.py: "
                         f"{bceval_read.TURN}; a place to start watching, not a finding). " + bceval_read.CAVEAT)
    for path, side, label in listed:
        seen = "; watched in an earlier audit" if label.startswith("leader") and str(path) in watched else ""
        # Cached per replay (the online cycle has read our losses already), else 0.1-1 s each at nice 19.
        turn = f"; {bceval_read.short(stored)}" if side and (stored := bceval_read.game(path, side, state)) else ""
        lines.append(f"- {path}  side {side or '?'}  ({label}{seen}{turn})")
    leaders = [str(p) for p, _, label in listed if label.startswith("leader")]
    if leaders and set(leaders) <= watched:
        lines += ["", "No leader game is new since earlier audits (leaders play few ranked games): skip them unless "
                      "a finding needs a second look."]
    if blind := blind_picks(state, rng or random.Random()):
        lines += ["", "Blind watch (AUDIT.md step 0): online losses and draws of the live build picked at random, "
                      "to watch before anything else:",
                  *[f"- {path}  side {side}  ({label})" for path, side, label in blind]]
    return "\n".join(lines) + "\n"


# The leader games this audit is given: kept in audit.json (not the worker's folder) as `leaders_given` while it
# runs, and added to `leaders_watched` only when it is applied, so the next context marks the ones already watched
# (five audits in a row were handed the same three games of rank 1 against team 776) and a failed audit marks none.
def leaders_given(state: Path) -> list[str]:
    return sorted(str(p) for p, _, label in games(state) if label.startswith("leader"))


# The runs whose saved losses this audit is given (games' local test games): kept in audit.json as `losses_given`
# while it is pending, so prune.done_with keeps them until it is applied or abandoned (the 18:18 audit of 2026-09-29
# listed E0340's losses, pruned at 18:42 once two newer results were kept, while it ran until 19:49).
def losses_given(state: Path) -> list[str]:
    return sorted({p.parent.parent.name for p, _, label in games(state) if label.startswith("local test game")})


# Opens an audit folder when one is due; returns it, or None.
def prepare(state: Path) -> Path | None:
    with meta.locked(state / "audit"):
        if not due(state):
            return None
        work = state / "audits" / str(time.time_ns())
        work.mkdir(parents=True)
        (meta.protected_io(state, "audits", work) / "PROMPT.md").write_text(meta.with_idea_rule(PROMPT.read_text()))
        (work / "context.md").write_text(context(state))
        briefing = state / "briefing.md"
        (work / "briefing.md").write_text(briefing.read_text() if briefing.exists() else "(no briefing yet)\n")
        # The leaders' gaps with us (AUDIT.md's method starts from them), as the review gets it.
        study = state / "study.md"
        (work / "study.md").write_text(study.read_text() if study.exists() else "(no study yet)\n")
        (work / "backlog.md").write_text(meta.waiting_ideas(state))
        # A copy: the worker reads no other audit folder, since it can write where it reads there.
        latest = state / "audits/latest.md"
        earlier = Path(latest.read_text().split(": ", 1)[-1].strip()) if latest.exists() else None
        if earlier and earlier.is_file():
            shutil.copy(earlier, work / "earlier-findings.md")
        save(state, {**read(state), "pending": str(work), "started_completed": results_count(state),
                     "leaders_given": leaders_given(state), "losses_given": losses_given(state)})
        return work


# Validates the audit's ideas.jsonl, queues them, and closes the audit.
def apply(state: Path, work: Path) -> str:
    with meta.locked(state / "audit"):
        record = read(state)
        if Path(record.get("pending", "")).resolve() != work.resolve():
            raise ValueError("not the pending audit")
        if not (work / "findings.md").exists():
            return f"audit incomplete: {work / 'findings.md'} is missing"
        ideas = work / "ideas.jsonl"
        refused = []
        proposals = meta.valid_proposals(meta.worker_file(ideas).splitlines() if ideas.exists() else [], MAX_IDEAS,
                                         refused=refused, ideas=meta.live_ideas(state))
        meta.report_refused(state, meta.protected_io(state, "audits", work), f"audit {work.name}", refused)
        added = meta.append_ideas(state, proposals, f"audit:{work.name}") if proposals else []
        latest = state / "audits/latest.md"
        latest.write_text(f"Latest replay audit: {work / 'findings.md'}\n")
        watched = sorted(set(record.get("leaders_watched", [])) | set(record.get("leaders_given", [])))
        save(state, {"completed": record["started_completed"], "finished_at": time.time(), "last": str(work),
                     "added": added, "leaders_watched": watched})
        return f"audit applied: {len(added)} ideas {added} ({work})"


alive = meta.alive


# The Claude driver's step, called before each claim: apply a finished background audit, relaunch a failed one
# after RETRY_S, or start a due one. Never waits for the worker. A launch that fails (it can't start, or its worker
# leaves no findings or unreadable ideas) counts toward meta.MAX_LAUNCHES, after which the folder is abandoned and the
# next tick prepares a fresh one, so nothing a worker leaves in its folder blocks audits for good; one a model
# provider's quota error stopped is retried once the limit resets but not counted (meta.provider_limited). The
# worker's stdin and output live in meta.protected_io, never in its folder. One caller at a time (meta.only_one):
# another returns None. Returns a line to log, or None.
def tick(state: Path) -> str | None:
    with meta.only_one(state / "audit") as mine:
        return launch_or_collect(state) if mine else None


# The upkeep tick's step (maintenance.tick): applies the pending audit once its background worker has exited (a
# recorded pid no longer alive), as tick would, and nothing else: it never prepares or launches an audit, and leaves a
# native session's audit (no pid) to that session. Returns collect's outcome line, or None.
def apply_finished(state: Path) -> str | None:
    with meta.only_one(state / "audit") as mine:
        if not mine:
            return None
        record = read(state)
        if not record.get("pending") or not record.get("pid") or alive(record["pid"]):
            return None
        return collect(state, Path(record["pending"]))


# Applies the pending audit `work` whose worker has exited (the caller holds meta.only_one), or records the failed
# launch (never relaunching, and `failed` alerts it); writes an applied audit to host-alerts.log for the orchestrator
# and returns the line. The apply runs in a fresh process of main's code (meta.fresh_line, MISSES 39): a driver's
# upkeep that imported an older valid_proposals would spend one of the audit's launches on ideas main accepts. A
# process that gives no outcome applies here instead (fresh_line).
def collect(state: Path, work: Path) -> str:
    try:
        meta.unlock(work)  # a folder the worker left at mode 000 would make a finished audit look failed
        message = meta.fresh_line("audit.py", ["apply", str(state), str(work)],
                                  ("audit applied", "audit incomplete"), lambda: applied_line(state, work))
    except Exception as error:
        message = f"audit incomplete: {type(error).__name__}: {error}"
    if message.startswith("audit incomplete"):
        # The session's error first: a worker a provider's quota stopped leaves no findings, and only its session.json
        # says why, which failed needs to see (meta.provider_limited).
        try:
            why = meta.session_error(meta.protected_io(state, "audits", work))
        except OSError as error:
            why = f"its session output couldn't be read ({type(error).__name__})"
        return failed(state, work, f"the worker failed: {f'{why}; ' if why else ''}{message}")
    meta.alert(state, message)
    return message


# tick's body, run while it holds meta.only_one.
def launch_or_collect(state: Path) -> str | None:
    record = read(state)
    work = Path(record["pending"]) if record.get("pending") else None
    if work and alive(record.get("pid")):
        return None
    if work and record.get("pid"):  # the worker has exited
        return collect(state, work)
    if quota.held(state, "audit") or work and time.time() < meta.when_due(record, "failed_at", RETRY_S):
        return None  # the usage level starts none (loop/quota.py), or a failed launch waits
    work = work or prepare(state)
    if work is None:
        return None
    try:
        folder = meta.protected_io(state, "audits", work)
        if not (folder / "PROMPT.md").exists():  # an audit prepared before its prompt was kept here
            (folder / "PROMPT.md").write_text(meta.with_idea_rule(PROMPT.read_text()))
        meta.unlock(work)
        settings = meta.worker_settings(state, "audits", work)
        command = meta.in_folder(work, meta.private_tmp(meta.worker_command(work, settings,
                                                                            meta.worker_reads(state, "audits"), usage="audit")))
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
    return f"audit launched in the background: {work}"


# Records a failed launch of the pending audit `work`: retried after RETRY_S, abandoned at meta.MAX_LAUNCHES counted
# failures (a provider's quota error is retried once the limit resets but not counted, meta.after_failure,
# meta.when_due); writes the line to host-alerts.log and returns it. Records nothing once `work` is no longer
# pending (a native `audited` applied it meanwhile), so the next audit's record never inherits a failure.
def failed(state: Path, work: Path, reason: str) -> str:
    with meta.locked(state / "audit"):
        record = read(state)
        if Path(record.get("pending", "")).resolve() != work.resolve():
            return f"audit {work} is no longer pending; nothing recorded ({reason})"
        record, abandoned = meta.after_failure(record, work, reason,
                                               ("completed", "finished_at", "last", "added", "leaders_watched"))
        save(state, record if abandoned else {**record, "failed_at": time.time()})
    if abandoned:
        message = f"audit abandoned after {meta.MAX_LAUNCHES} failed launches ({work}): {reason}"
    elif meta.provider_limited(reason):
        message = (f"audit waiting out a provider quota limit, retried {meta.retry_when(record)} (not counted) "
                   f"({work}): {reason}")
    else:
        message = f"audit worker failed; retrying after an hour ({work}): {reason}"
    meta.alert(state, message)
    return message


# `audit.py apply <state> <work>`'s outcome: apply's line, or its failure as an incomplete audit. collect runs it in a
# fresh process (meta.fresh_line).
def applied_line(state: Path, work: Path) -> str:
    try:
        return apply(state, work)
    except Exception as error:
        return f"audit incomplete: {type(error).__name__}: {error}"


if __name__ == "__main__":
    if sys.argv[1:2] != ["apply"] or len(sys.argv) != 4:
        sys.exit("usage: audit.py apply <loop home> <audit folder>")
    print(applied_line(Path(sys.argv[2]), Path(sys.argv[3])))
