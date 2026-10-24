"""Checks every listener format (tools/sonar/spec.toml) against replays with the bot's own decoder and detector.

The bot folder's listen.hpp is compiled into a small probe, so what is checked is what the bot runs. Every team's ranked games
(Cache me outside, 952: unranked too), newest first, with loop/decoys.py's flagged versions left out and counted, are
read up to --max-games a team; each game's values of that team (at most PER_GAME, evenly spaced) go through every
format and, in send order, through a fresh Listener. A decoded fact is true when a dragon of the team (of its enemy,
for a sighting) had its head there within a round of the fact's round, with the stated length give or take one.
Per format, over the team's game groups (a submission, or a day once the site stopped naming them) where it is in use
(it recognises DETECT_COVER of their values):

    values recognised; cover (the best group's share of the team's values recognised); head (share of recognised
    values whose head is true); match (head and length true); stray (the largest share of one other team's values in
    one game it recognises, and that team)

A report is verified at head >= VERIFIED_MATCH on VERIFIED_MIN values or more; a format detects its team when its
cover is DETECT_COVER or more and at least DETECT_RATIO times its stray. The report also lists, per team, which entries
cover which groups, the groups no entry covers (new or changed formats), and the stream check: how many games' values
lock the Listener onto the right team, a wrong one, or none. --update writes the measurements to
tools/sonar/measured.json (then run formats.py).

    .venv/bin/python tools/sonar/verify.py --bot <folder with listen.hpp> [--team 91 ...] [--since ISO]
        [--max-games N] [--out report.md] [--update] [--cache DIR] [--index extra/index.jsonl ...]
"""

from __future__ import annotations

import argparse
import collections
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.append(str(HERE))
sys.path.append(str(HERE.parents[1] / "loop"))

import formats  # noqa: E402
import games as G  # noqa: E402

VERIFIED_MATCH = 0.95  # share of recognised values whose head is true
VERIFIED_MIN = 50
DETECT_COVER = 0.3
DETECT_RATIO = 4  # a recognised value must be at least this much likelier from the team than from any other
STRAY_MIN = 20  # values a team's game needs before its share counts as a format's stray
PER_GAME = 600

PROBE = r"""
#include "listen.hpp"
#include <cstdio>
#include <vector>
// Reads "team W H round value" lines, one team's values of one game in send order. Prints, per line, every format of
// that team that decodes the value as "index,x,y,length,round,id,sighting" separated by spaces; at the end
// "STRAY index:count ..." (values each format decoded of teams other than its own) and "LOCK team values" (the team a
// fresh Listener fed these values locked onto, -1 none, and after how many values).
int main() {
    int team, W, H, round;
    unsigned long long v;
    std::vector<long> stray(bot::listen::FORMATS.size());
    bot::listen::Listener listener;
    int heard = 0, lockedAt = -1;
    while (std::scanf("%d %d %d %d %llu", &team, &W, &H, &round, &v) == 5) {
        for (int i = 0; i < static_cast<int>(bot::listen::FORMATS.size()); i++) {
            auto const f = bot::listen::decode(i, v, round, W, H);
            if (!f)
                continue;
            if (bot::listen::FORMATS[i].team != team)
                stray[i]++;
            else
                std::printf("%d,%d,%d,%d,%d,%d,%d ", i, f->x, f->y, f->length, f->round, f->id, f->sighting ? 1 : 0);
        }
        std::printf("\n");
        listener.offer(v, round, W, H);
        heard++;
        if (lockedAt < 0 && listener.team() >= 0)
            lockedAt = heard;
    }
    std::printf("STRAY");
    for (std::size_t i = 0; i < stray.size(); i++)
        if (stray[i])
            std::printf(" %zu:%ld", i, stray[i]);
    std::printf("\nLOCK %d %d\n", listener.team(), lockedAt);
}
"""


def build_probe(bot: Path, directory: Path) -> Path:
    source, binary = directory / "probe.cpp", directory / "probe"
    source.write_text(PROBE)
    subprocess.run(["clang++", "-std=c++20", "-O2", "-I", str(bot), str(source), "-o", str(binary)],
                   check=True, capture_output=True, text=True)
    return binary


# A team version's label: its named submission, else the day of the game.
def group_of(meta: dict, side: int) -> str:
    sid = meta["sB"] if side else meta["sA"]
    return f"submission {sid}" if sid else f"day {meta['at'][:10]}"


def _stamp(at: str) -> float:
    try:
        return datetime.fromisoformat(at.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


# The games to check per team (newest first, at most `max_games`) with loop/decoys.py's flagged latest version left
# out: ({team: [(meta, path, side)]}, {team: {kept, dropped, available}}).
def select(per_team: dict[int, list], max_games: int) -> tuple[dict, dict]:
    import decoys
    chosen, counts = {}, {}
    for team, rows in per_team.items():
        scored = []
        for meta, _, side in rows:
            won = meta.get("winner")
            scored.append((meta["sB"] if side else meta["sA"], _stamp(meta["at"]),
                           0.5 if won is None else float((won == "b") == bool(side))))
        dropped: set[int] = set()
        result = decoys.check(scored)
        if result and result["flagged"]:
            first = datetime.fromtimestamp(result["first_at"], timezone.utc).isoformat()
            dropped = {meta["match"] for meta, _, side in rows
                       if result["latest"] == f"submission {meta['sB'] if side else meta['sA']}"
                       or (result["latest"].startswith("unnamed") and meta["at"] >= first)}
        chosen[team] = [r for r in rows if r[0]["match"] not in dropped][:max_games]
        counts[team] = dict(kept=len(chosen[team]), dropped=len(dropped), available=len(rows))
    return chosen, counts


# (head true, head and length true) for `fact` in `truth` ({(round, side): {(x, y): [length, ...]}}).
def matches(fact: tuple, side: int, truth: dict) -> tuple[bool, bool]:
    _, x, y, length, rnd, _, sighting = fact
    who = 1 - side if sighting else side
    head = False
    for q in (rnd - 1, rnd, rnd + 1):
        lengths = truth.get((q, who), {}).get((x, y))
        if lengths:
            head = True
            if length < 0 or any(abs(length - n) <= 1 for n in lengths):
                return True, True
    return head, False


# Runs every chosen game through the probe: the raw counts measure() and report() read.
def tally(chosen: dict, bot: Path, cache: Path) -> dict:
    t = dict(by_group=collections.defaultdict(collections.Counter), group_values=collections.Counter(),
             group_games=collections.defaultdict(set), game_values=collections.Counter(),
             stray=collections.defaultdict(collections.Counter), locks=collections.Counter(),
             wrong=collections.Counter(), lock_after=collections.defaultdict(list))
    need = collections.defaultdict(list)
    for team, rows in chosen.items():
        for meta, path, side in rows:
            need[meta["match"]].append((team, side, meta, path))
    with tempfile.TemporaryDirectory(prefix="sonar-verify-") as directory:
        probe = build_probe(bot, Path(directory))
        for match, entries in need.items():
            try:
                game = G.extract(entries[0][2], entries[0][3], cache)
            except Exception as error:  # an unreadable replay is skipped and said
                print(f"skip {match}: {error!r}", file=sys.stderr)
                continue
            truth: dict = collections.defaultdict(lambda: collections.defaultdict(list))
            st, p = game["states"], game["pings"]
            for i in range(len(st["r"])):
                truth[(st["r"][i], st["t"][i])][(st["x"][i], st["y"][i])].append(st["len"][i])
            for team, side, meta, _ in entries:
                idx = G.sent_by(game, side)
                idx = idx[:: max(1, len(idx) // PER_GAME)]
                group = (team, group_of(meta, side))
                t["group_games"][group].add(match)
                t["group_values"][group] += len(idx)
                if not idx:
                    continue
                lines = "".join(f"{team} {game['W']} {game['H']} {p['r'][i]} {p['v'][i]}\n" for i in idx)
                out = subprocess.run([str(probe)], input=lines, capture_output=True, text=True, check=True).stdout
                body, _, tail = out.rpartition("STRAY")
                stray_part, _, lock = tail.partition("\nLOCK ")
                for item in stray_part.split():
                    i, n = item.split(":")
                    t["stray"][int(i)][(team, match)] += int(n)
                locked, after = (int(x) for x in lock.split())
                t["locks"]["right" if locked == team else "none" if locked < 0 else "wrong"] += 1
                if locked == team:
                    t["lock_after"][team].append(after)
                elif locked >= 0:
                    t["wrong"][(team, locked)] += 1
                t["game_values"][(team, match)] = len(idx)
                for line in body.splitlines():
                    facts = [tuple(int(v) for v in item.split(",")) for item in line.split()]
                    for fact in facts:
                        head, ok = matches(fact, side, truth)
                        t["by_group"][(fact[0], group[1])].update(recognised=1, true=ok, head=head)
    return t


# Each format's measurement from `t`: {"team/version": {cover, stray, stray_team, detects, verified, values, head,
# match, groups: {group: recognised share}}}.
def measure(specs: list[dict], t: dict) -> dict:
    out = {}
    for i, fmt in enumerate(specs):
        team = fmt["team"]
        groups = {v: c for (j, v), c in t["by_group"].items() if j == i}
        share = {v: c["recognised"] / max(1, t["group_values"][(team, v)]) for v, c in groups.items()}
        in_use = [c for v, c in groups.items() if share[v] >= DETECT_COVER]
        s = sum(in_use or groups.values(), collections.Counter())
        rec = s["recognised"]
        # The best group's share (of two games or more, else over all the team's games), so a version the team has
        # since dropped still names it.
        total = sum(n for (tm, _), n in t["group_values"].items() if tm == team)
        cover = max((share[v] for v in groups if len(t["group_games"][(team, v)]) >= 2),
                    default=sum(c["recognised"] for c in groups.values()) / max(1, total))
        stray, stray_team = max(((n / t["game_values"][g], g[0]) for g, n in t["stray"][i].items()
                                 if t["game_values"][g] >= STRAY_MIN), default=(0.0, None))
        head = s["head"] / rec if rec else 0.0
        out[f"{team}/{fmt['version']}"] = dict(
            cover=round(cover, 4), stray=stray, stray_team=stray_team, values=rec, head=round(head, 4),
            match=round(s["true"] / rec, 4) if rec else 0.0,
            detects=rec >= VERIFIED_MIN and cover >= DETECT_COVER and stray * DETECT_RATIO <= cover,
            verified=fmt["kind"] == "report" and rec >= VERIFIED_MIN and head >= VERIFIED_MATCH,
            groups={v: round(x, 3) for v, x in sorted(share.items())})
    return out


# Per team, its game groups that sent `min_values` values or more and that no trusted entry (in `trusted`, keys
# "team/version": by default those `measured` says detect or verify) covers, i.e. recognises DETECT_COVER of:
# {team: [group, ...]}, the teams whose format is new or changed.
def uncovered(specs: list[dict], measured: dict, t: dict, trusted: set[str] | None = None,
              min_values: int = 200) -> dict[int, list[str]]:
    if trusted is None:
        trusted = {k for k, m in measured.items() if m["detects"] or m["verified"]}
    covered = collections.defaultdict(set)
    for fmt in specs:
        key = f"{fmt['team']}/{fmt['version']}"
        if key in trusted:
            covered[fmt["team"]] |= {v for v, x in measured[key]["groups"].items() if x >= DETECT_COVER}
    out: dict[int, list[str]] = {}
    for (team, group), n in t["group_values"].items():
        if n >= min_values and group not in covered[team]:
            out.setdefault(team, []).append(group)
    return {team: sorted(g) for team, g in sorted(out.items())}


def report(specs: list[dict], measured: dict, t: dict, counts: dict) -> str:
    lines = ["| team | name | version | values | cover | head | match | stray (team) | verdict |",
             "| ---: | :--- | :--- | ---: | ---: | ---: | ---: | ---: | :--- |"]
    for fmt in specs:
        m = measured[f"{fmt['team']}/{fmt['version']}"]
        verdict = ("verified" if m["verified"] else "unverified") if fmt["kind"] == "report" else fmt["kind"]
        verdict += ", detects" if m["detects"] else ""
        who = f" ({m['stray_team']})" if m["stray_team"] else ""
        lines.append(f"| {fmt['team']} | {fmt['name']} | {fmt['version']} | {m['values']} | {m['cover']:.3f} | "
                     f"{m['head']:.3f} | {m['match']:.3f} | {m['stray']:.2g}{who} | {verdict} |")
    lines += ["", "Versions per team (entries that detect or verify, and the groups where they recognise "
              f"{DETECT_COVER} or more of the values):"]
    for team in sorted({team for team, _ in t["group_values"]}):
        parts = []
        for fmt in specs:
            m = measured[f"{fmt['team']}/{fmt['version']}"]
            used = [v for v, x in m["groups"].items() if x >= DETECT_COVER]
            if fmt["team"] == team and (m["detects"] or m["verified"]) and used:
                parts.append(f"{fmt['version']}: {', '.join(used)}")
        silent = sorted(g for (tm, g), n in t["group_values"].items() if tm == team and n == 0)
        parts += [f"silent: {', '.join(silent)}"] if silent else []
        lines.append(f"- {team}: " + ("; ".join(parts) or "none"))
    lines += ["", "Groups no detecting or verified entry covers (new or changed formats, keyed or unknown):"]
    lines += [f"- {team}: {', '.join(groups)}" for team, groups in uncovered(specs, measured, t).items()]
    after = sorted(a for v in t["lock_after"].values() for a in v)
    lines += ["", f"Stream check: {t['locks']['right']} games lock onto the right team (median after "
              f"{after[len(after) // 2] if after else '-'} values), {t['locks']['wrong']} onto another, "
              f"{t['locks']['none']} none. Wrong locks (sender -> locked: games): "
              + ", ".join(f"{a} -> {b}: {n}" for (a, b), n in sorted(t["wrong"].items()))]
    lines += ["", "Games per team: kept, dropped as possible decoys (loop/decoys.py), available:"]
    lines += [f"- {team}: {c['kept']} kept, {c['dropped']} dropped, {c['available']} available"
              for team, c in sorted(counts.items())]
    return "\n".join(lines) + "\n"


# Measures every spec entry over the chosen games; with `update`, merges the measurements into measured.json.
def run(bot: Path, teams: set[int] | None = None, since: str | None = None, max_games: int = 1000,
        cache: Path = G.CACHE, extra: tuple[Path, ...] = (), update: bool = False) -> tuple[list, dict, dict, dict]:
    specs = formats.load()
    per_team = G.team_games(G.games(extra), since)
    if teams:
        per_team = {tm: rows for tm, rows in per_team.items() if tm in teams}
    chosen, counts = select(per_team, max_games)
    t = tally(chosen, bot, cache)
    measured = measure(specs, t)
    if update:
        old = json.loads(formats.MEASURED.read_text()) if formats.MEASURED.exists() else {}
        old.update(measured)
        formats.MEASURED.write_text(json.dumps(old, indent=1, sort_keys=True) + "\n")
    return specs, measured, t, counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bot", type=Path, default=formats.BOT, help="a bot folder with listen.hpp")
    parser.add_argument("--team", type=int, nargs="*", help="only these teams' games")
    parser.add_argument("--since", help="only games from this ISO time (UTC), e.g. 2026-10-10T08:00")
    parser.add_argument("--max-games", type=int, default=1000, help="newest games per team")
    parser.add_argument("--out", help="write the report here (markdown), else print it")
    parser.add_argument("--update", action="store_true", help="merge the measurements into tools/sonar/measured.json")
    parser.add_argument("--cache", type=Path, default=G.CACHE)
    parser.add_argument("--index", type=Path, nargs="*", default=[], help="more index.jsonl files of replays")
    args = parser.parse_args()
    specs, measured, t, counts = run(args.bot, set(args.team or []) or None, args.since, args.max_games, args.cache,
                                     tuple(args.index), args.update)
    text = report(specs, measured, t, counts)
    if args.out:
        Path(args.out).write_text(text)
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
