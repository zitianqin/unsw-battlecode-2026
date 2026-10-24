"""Evidence for lanes: what the current champion dies to and how it loses, from real games with replays.

Run from the main checkout:   uv run loop/evidence.py [--maps 6]
(The driver runs it after setup and, in the background, after every promotion, with the current toolkit.) The champion plays the screen
opponents in SCREEN on random generated maps, both sides: 4 x 2 x --maps games (48 at the default). Writes
~/.bc-loop/evidence.md, which is pasted into every lane prompt and read by the meta session. Every number comes from
the engine-checked replay tools, so lanes reason from facts rather than guesses; a lost game's bceval turning point is
a model's reading (tools/bceval_read.py), given with its caveat.
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import driver

STATE = driver.STATE
REFEREE = STATE / "referee"
CHAMPION = STATE / "champion" / "bot"
EVIDENCE = STATE / "evidence.md"
TRAIN_MAPS = STATE / "maps-train"

# The referee/gauntlet.toml opponents the champion is shown against (review M14): a hard folder, a grower with
# fewer, longer dragons, a swarm that never folds, and an early king folding from round 280 (review M21). The old
# pool of toolkit examples only ever lost to it.
SCREEN = ("folder", "grower", "swarm", "earlyking")

sys.path.insert(0, str(REFEREE / "tools"))

# Games run through the referee's CLI in a subprocess. The replay tools' schema has its own parser, so it may share a
# process with the referee's run.py (MISSES 97).
from replays import analyse, load_replay  # noqa: E402

CAUSES = ["hitWall", "hitSelf", "hitOtherBody", "hitHeadToHead", "noValidAction"]


# One evidence game from the champion's side: the referee's result, plus what its replay shows when it was kept.
@dataclass
class Played:
    opponent: str
    map: str
    side: str
    winner: str  # "a" (the champion), "b" or "draw"
    end: str  # "rounds" or "elimination"
    rounds: int
    ours: int  # longest dragon at the end, ours and theirs
    theirs: int
    deaths: Counter[str] = field(default_factory=Counter)
    turning: str = ""
    total: int = 0  # our total length and dragons alive at the end
    alive: int = 0
    # From the replay: our second-longest at the end (review M15), our dragons alive at round 100 and pearls eaten
    # in rounds 0-99 (review M9).
    second: int | None = None
    alive100: int | None = None
    pearls100: int | None = None
    # From the replay, when the game reached round 400: each side's longest dragon and total length there (review M21).
    longest400: int | None = None
    theirs400: int | None = None
    total400: int | None = None
    theirs_total400: int | None = None
    bceval: str = ""  # a lost game's bceval turning point (bceval_read.short), "" while bceval is off


def turning_point(game, us: str) -> str:
    """The first round our dragon count fell clearly behind (by 5 or by a quarter), if it ever did."""
    them = "B" if us == "A" else "A"
    for snap in game.snapshots:
        ours, theirs = snap.alive[us], snap.alive[them]
        if theirs - ours >= max(5, theirs // 4):
            return f"fell behind at round {snap.round} ({ours} vs {theirs} dragons alive)"
    return "never clearly behind on dragon count"


def play(name: str, bot: Path, maps: list[Path], workers: str) -> list[Played]:
    """The champion against one opponent on `maps`, both sides, with each game's replay read."""
    replays = STATE / "evidence-replays" / name
    out = replays / "results.jsonl"
    shutil.rmtree(replays, ignore_errors=True)  # replay files are matched to results by index
    replays.mkdir(parents=True)
    # As a tune player beside an evaluation (native.host_share: it splits the tune's one share with a playing tune,
    # on an 8+-core host under 2 * native.YIELDING cores only where the share's rationed slot comes up during a host
    # stage, and the screen fillers step aside for it), with its own host status file, never the evaluation's: the
    # refresh holds no evaluation lock (driver.refresh_evidence).
    result = driver.referee("referee/run.py", ["--a", str(CHAMPION), "--b", str(bot), "--maps", *map(str, maps),
                                               "--workers", workers, "--replays", str(replays), "--out", str(out)],
                            yielding="tune", status=STATE / "remote-status-evidence.json")
    if result.returncode != 0:
        raise RuntimeError(f"evidence games against {name} failed: {result.stderr[-500:]}")
    played = []
    for i, line in enumerate(out.read_text().splitlines() if out.exists() else []):
        row = json.loads(line)
        if row["error"]:
            continue
        game = Played(name, Path(row["map"]).stem, row["a_side"], row["winner"], row["end"], row["rounds"],
                      row["a"]["longest"], row["b"]["longest"], total=row["a"]["total"], alive=row["a"]["alive"])
        if replay := sorted(replays.glob(f"{i:04d}-*.replay")):
            analysed = analyse(load_replay(replay[0]))
            game.deaths = Counter(analysed.teams[game.side].deaths)
            game.turning = turning_point(analysed, game.side)
            # A referee checkout older than the replay tools' second-longest has no `second`.
            game.second = getattr(analysed.snapshots[-1], "second", {}).get(game.side)
            game.alive100 = next((s.alive[game.side] for s in analysed.snapshots if s.round >= 100),
                                 analysed.snapshots[-1].alive[game.side])
            game.pearls100 = analysed.teams[game.side].pearls_by_phase["early"]
            read_round_400(game, analysed.snapshots)
            if game.winner == "b" and (stored := driver.online.bceval_module().game(replay[0], game.side, STATE)):
                game.bceval = driver.online.bceval_module().short(stored)  # 0.1-1 s a loss at nice 19
        played.append(game)
    return played


# Fills `game`'s round-400 lengths, ours (game.side) and theirs, from a replay's per-round snapshots; leaves them None
# when the game ended before round 400.
def read_round_400(game: Played, snapshots: list) -> None:
    if at400 := next((s for s in snapshots if s.round >= 400), None):
        them = "B" if game.side == "A" else "A"
        game.longest400, game.theirs400 = at400.longest[game.side], at400.longest[them]
        game.total400, game.theirs_total400 = at400.total[game.side], at400.total[them]


def mean(values: list[float]) -> str:
    return f"{sum(values) / len(values):.1f}" if values else "-"


# The mean of part / whole over (part, whole) pairs with a nonzero whole, to two places, or "-".
def mean_share(pairs: list[tuple[int, int]]) -> str:
    shares = [part / whole for part, whole in pairs if whole]
    return f"{sum(shares) / len(shares):.2f}" if shares else "-"


def opponent_line(name: str, games: list[Played]) -> str:
    """'- folder: 12 games, 9-3-0 (W-L-D); 10 went to round 499, longest ours - theirs there mean +4.2 (-6 to +15),
    2 below 0; our second-longest there 14.0, total 61.2, <=8 dragons in 7; longest at round 400 ours 6.0, theirs
    18.0; longest(r499)/total(r400) ours 0.22, theirs 0.32. At round 100 (means): 9.5 alive, 31.0 pearls eaten.'"""
    record = "-".join(str(sum(g.winner == w for g in games)) for w in ("a", "b", "draw"))
    full = [g for g in games if g.end == "rounds"]
    margins = [g.ours - g.theirs for g in full]
    text = f"- {name}: {len(games)} games, {record} (W-L-D); {len(full)} went to round 499"
    if full:
        text += (f", longest ours - theirs there mean {sum(margins) / len(margins):+.1f} ({min(margins):+d} to "
                 f"{max(margins):+d}), {sum(m < 0 for m in margins)} below 0; our second-longest there "
                 f"{mean([g.second for g in full if g.second is not None])}, total {mean([g.total for g in full])}, "
                 f"<=8 dragons in {sum(g.alive <= 8 for g in full)}")
        if known := [g for g in full if g.longest400 is not None]:
            text += (f"; longest at round 400 ours {mean([g.longest400 for g in known])}, theirs "
                     f"{mean([g.theirs400 for g in known])}; longest(r499)/total(r400) ours "
                     f"{mean_share([(g.ours, g.total400) for g in known])}, theirs "
                     f"{mean_share([(g.theirs, g.theirs_total400) for g in known])}")
    opening = [g for g in games if g.alive100 is not None]
    if opening:
        text += (f". At round 100 (means): {mean([g.alive100 for g in opening])} alive, "
                 f"{mean([g.pearls100 for g in opening])} pearls eaten")
    return text + "."


def lost_line(game: Played) -> str:
    how = (f"on length in round {game.rounds}, longest {game.ours} vs {game.theirs}" if game.end == "rounds"
           else f"by elimination in round {game.rounds}")
    return (f"- vs {game.opponent} on {game.map} (we were side {game.side}): lost {how}; {game.turning or 'no replay'}; "
            f"our deaths {dict(game.deaths)}" + (f"; bceval {game.bceval}" if game.bceval else ""))


def evidence_text(champion: str, epoch: str, map_count: int, games: list[Played]) -> str:
    """evidence.md from the played games: record and round-499 margins per opponent, deaths, and every lost game."""
    names = list(dict.fromkeys(g.opponent for g in games))
    deaths = sum((g.deaths for g in games), Counter())
    lost = [g for g in games if g.winner == "b"]
    lines = [
        f"Champion commit: {champion}",
        f"Toolchain: {epoch} (do not pool different engine versions)",
        "",
        f"Champion vs the screen opponents {', '.join(names)} (referee/gauntlet.toml) on {map_count} random generated "
        f"maps, both sides: {len(games)} games. A small sample for what it dies to and loses to, not a strength "
        "estimate.",
        *(opponent_line(name, [g for g in games if g.opponent == name]) for name in names),
        "",
        "Our deaths per game by cause: "
        + ", ".join(f"{cause} {deaths[cause] / max(len(games), 1):.1f}" for cause in CAUSES) + ".",
        "",
        f"Lost games ({len(lost)}):" if lost else "No lost games in this sample.",
        *map(lost_line, lost),
    ]
    if any(g.bceval for g in lost):
        bceval_read = driver.online.bceval_module()
        lines += ["", f"bceval's turning point in a lost game: {bceval_read.TURN} (tools/bceval_read.py). "
                  + bceval_read.CAVEAT]
    return "\n".join(lines) + "\n"


# The champion checkout's commit; "" for a checkout git can't read (mid-rewrite) unless `check`.
def champion_commit(check: bool = False) -> str:
    return driver.git("rev-parse", "HEAD", cwd=STATE / "champion", check=check)


# The evidence text for the champion checkout's commit, or None when a promotion moved the checkout while its games
# played (they may then have played either champion; the promotion's own refresh follows). A failure raises, unless
# the checkout moved meanwhile (a promotion rewriting the champion's bot mid-build fails its games): None then too.
def build_evidence(map_count: int, workers: str = "auto") -> str | None:
    champion = champion_commit(check=True)
    try:
        maps = sorted(TRAIN_MAPS.glob("*.map"))
        chosen = random.Random(time.time()).sample(maps, min(map_count, len(maps)))
        games = [g for name, bot in driver.rotation_bots(list(SCREEN)) for g in play(name, bot, chosen, workers)]
    except Exception:
        if champion_commit() != champion:
            return None
        raise
    if champion_commit() != champion:
        return None
    return evidence_text(champion, driver.maintenance.toolchain(STATE)["epoch"], len(chosen), games)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--maps", type=int, default=6)
    parser.add_argument("--workers", default="auto")
    args = parser.parse_args()
    text = build_evidence(args.maps, args.workers)
    if text is None:
        print("the champion moved during the games; evidence.md not written", file=sys.stderr)
        raise SystemExit(driver.EVIDENCE_MOVED)
    EVIDENCE.write_text(text)
    print(text)


if __name__ == "__main__":
    main()
