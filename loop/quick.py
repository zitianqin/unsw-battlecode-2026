"""Quick check for a lane: 24 games against the champion, then a CPU check in the judge's sandbox.

Run from the main checkout, through the gated runner, with the clone's Python (its pinned toolkit):
    cd <main> && <clone>/.venv/bin/python loop/native.py play loop/quick.py --clone <clone>
It plays the clone's bot/ against its .champion/bot on the main checkout's maps with its referee: the clone's own
loop/ and referee/ are loop/champion's, days old.
Prints one line, e.g.        quick: 12-7-1 vs champion (score 0.62) | failures 0 | cpu ok p99 18.2M max 31.0M

The candidate plays the 24 games as its logging copy (P.debugIndicator on, which only adds the debug line), so a
no-valid-action death whose dying turn's line says "capfeed fire" (P.capFeedInPlace, by design) is left out of the
failures and named (capfeed_deaths).

Twenty-four games can't measure small effects. This catches crashes, timeouts, CPU overruns and big regressions
before the referee spends hundreds of games on the candidate. The keep/discard decision is never made here.

The 24 games play here and on the SSH game machines at a coder's share (pair_games); the CPU check's two sandbox games
play here and on the hosts of this Mac's platform, where a sandbox game prices as here (cpu_line), and give up after
CPU_WAIT_S in all with neither playing (native.batch_slots). It holds no evaluation lock (native.quick), so nothing
waits for it.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent  # the maps and referee: the main checkout's (the bots: --clone's)
sys.path.insert(0, str(REPO / "referee"))

import native  # noqa: E402  (under `native.py play`, the running module: native.play_script)
import remote  # noqa: E402
import run  # noqa: E402
from run import GameResult, Job, play_pairs  # noqa: E402

LADDER = ["trophy", "portals", "queen_of_spades", "devil", "slithery_fight"]
# The Sprint's maps (and ladder maps since 2026-09-26): each team starts with one 11-14-long dragon, which catches
# a crash or CPU overrun the short starters never reach.
SPRINT = ["dilemma", "autarky"]
CPU_MAP = REPO / "maps" / "ladder" / "schooltime.map"
P99_LIMIT = 50_000_000
MAX_LIMIT = 90_000_000
CPU_WAIT_S = native.CPU_WAIT_S  # how long the CPU check may wait in all with neither game playing (cpu_line)
UNCHECKED = native.CPU_UNCHECKED


def quick_maps() -> list[Path]:
    """Five ladder maps, the two Sprint maps and five fixed generated ones: 12 maps, 24 games with sides swapped."""
    generated = sorted((REPO / "maps" / "gen-fixed").glob("*.map"))[:5]
    return ([REPO / "maps" / "ladder" / f"{name}.map" for name in LADDER]
            + [REPO / "maps" / "sprint" / f"{name}.map" for name in SPRINT] + generated)


def failures(results: list[GameResult], by_design: dict[int, int] | None = None) -> int:
    """Turns our bot lost to timeouts, crashes or no valid action, plus games that couldn't run at all; `by_design`
    (a game's count of our deliberate no-valid-action deaths, capfeeds) is left out of each game's."""
    count = 0
    for k, result in enumerate(results):
        if result.error or result.a is None:
            count += 1
            continue
        count += result.a.timeouts + result.a.crashes + result.a.no_action_deaths - (by_design or {}).get(k, 0)
    return count


# The candidate as the quick games play it: a copy with P.debugIndicator on, which only adds the debug line each turn
# (play is unchanged; the CPU check plays the candidate itself), so a death's line can say why it died. A bot without
# the switch is played as it is.
def logging_copy(cand: Path, into: Path) -> Path:
    copy = into / "cand"
    shutil.copytree(cand, copy, ignore=shutil.ignore_patterns(".*"))
    params = copy / "params.hpp"
    if params.exists():
        params.write_text(re.sub(r"(\bdebugIndicator\s*=\s*)false\b", r"\1true", params.read_text()))
    return run.content_named_copy(copy, into)


# For each game (by index) with a no-valid-action death of ours, how many of them were capfeed fires: P.capFeedInPlace
# dies where it stands by an illegal split, by design, and says "capfeed fire" in the dying turn's debug line (E0411 on
# gen_normal_10, 30 Sep: a boxed-in feeder at r473 flagged as a failure). Read from the game's replay in `replays`
# (run.py names them <index>-<map>-a<side>.replay); a replay that can't be read excuses nothing.
def capfeed_deaths(results: list[GameResult], replays: Path) -> dict[int, int]:
    sys.path[:0] = [p for p in (str(REPO / "tools"), str(REPO)) if p not in sys.path]
    import watch
    found = {}
    for k, result in enumerate(results):
        if result.a is None or not result.a.no_action_deaths:
            continue
        try:
            (path,) = replays.glob(f"{k:04d}-*-a{result.a_side}.replay")
            w = watch.watch(path)
        except Exception:
            continue
        lines = {(r, dragon): text for r, dragon, _, text in w.logs}
        found[k] = sum(d["team"] == result.a_side and d["reason"] == "noValidAction"
                       and "capfeed fire" in lines.get((d["round"], d["id"]), "") for d in w.game.deaths)
    return found


def score_line(results: list[GameResult]) -> str:
    wins = sum(r.winner == "a" for r in results)
    losses = sum(r.winner == "b" for r in results)
    draws = sum(r.winner == "draw" for r in results)
    played = wins + losses + draws
    score = (wins + draws / 2) / played if played else 0.0
    return f"{wins}-{losses}-{draws} vs champion (score {score:.2f})"


# The CPU check: the candidate against itself on CPU_MAP, both sides, in the judge's sandbox, here or on a host of
# this Mac's platform at the coder's share (sandbox_hosts), UNCHECKED once it has waited CPU_WAIT_S in all with no game
# playing anywhere. Until 29 Sep it played here only, after a wait of at most CPU_WAIT_S for one slot here: E0317's and
# E0321's got through that wait and then waited from about 10:10 for hours at 0% CPU, the gate holding the game itself.
def cpu_line(cand: Path) -> str:
    hosts, gate = remote.load_hosts(), run.game_slots
    run.game_slots = native.batch_slots(elsewhere=lambda: any(host.active for host in hosts), say=False)
    try:
        results = run.play_games([Job(CPU_MAP, cand, cand, side) for side in ("A", "B")], "auto", True,
                                 sandbox_hosts=True)
    except SystemExit:  # batch_slots: waited CPU_WAIT_S with nothing playing
        return UNCHECKED
    except Exception as error:  # the lane still gets its game results if the sandbox can't start here
        return f"cpu not checked ({type(error).__name__})"
    finally:
        run.game_slots = gate
    points = [side.points for r in results for side in (r.a, r.b) if side is not None and side.points is not None]
    if not points or failures(results):
        return "cpu FAIL (crash or timeout in the sandbox)"
    p99, peak = max(p.p99 for p in points), max(p.max for p in points)
    verdict = "ok" if p99 <= P99_LIMIT and peak <= MAX_LIMIT else "FAIL"
    return f"cpu {verdict} p99 {p99 / 1e6:.1f}M max {peak / 1e6:.1f}M"


# The 24 games against the champion, on this Mac and the game machines, yielding to a running evaluation as a coder's
# own games do (native.play_games), with their hosts' status in the coder's file so the utilisation record shows them.
# Until 2026-09-27 they played 3 at a time on this Mac only, waiting out its load while the hosts sat idle (MISSES 49).
def pair_games(cand: Path, base: Path, clone: Path, replays: Path | None = None) -> list[GameResult]:
    native.yield_to_evaluations(remote, lane=native.coder_lane(clone))
    remote.STATUS = native.coder_status(clone.name)
    return play_pairs(cand, base, quick_maps(), workers="auto", replays=replays)


def main() -> None:
    parser = argparse.ArgumentParser(description="24 games against the champion and a sandbox CPU check.")
    parser.add_argument("--clone", type=Path, required=True, help="the lane's clone: its bot/ and .champion/bot")
    clone = parser.parse_args().clone.resolve()
    cand, base = clone / "bot", clone / ".champion" / "bot"
    if not base.exists():
        sys.exit(f"no champion copy at {base} (the loop driver puts it there)")
    with tempfile.TemporaryDirectory(prefix="quick-") as scratch:
        replays = Path(scratch) / "replays"
        replays.mkdir()
        results = pair_games(logging_copy(cand, Path(scratch)), base, clone, replays)
        by_design = capfeed_deaths(results, replays)
    cpu = cpu_line(cand)
    fed = sum(by_design.values())
    print(f"quick: {score_line(results)} | failures {failures(results, by_design)}"
          + (f" ({fed} capfeed fire{'s' if fed > 1 else ''} left out)" if fed else "") + f" | {cpu}")


if __name__ == "__main__":
    main()
